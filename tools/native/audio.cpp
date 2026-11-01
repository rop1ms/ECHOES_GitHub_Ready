// Continuous VLC -> PortAudio path. Neither native callback enters Python.
// Fixed SPSC queues: the device callback never allocates or waits for a lock.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <new>
#include <vector>

#define API extern "C" __declspec(dllexport)
constexpr unsigned SR=48000, CAP=131072, MASK=CAP-1, VIS=131072;
enum State { Idle, Sync, Play, Chained, Done, IdlePlay };
struct Mixer;
struct Frame { float l,r; int64_t pts; uint64_t gen; };
static double now_s() {
    LARGE_INTEGER n,f; QueryPerformanceCounter(&n); QueryPerformanceFrequency(&f);
    return double(n.QuadPart)/double(f.QuadPart);
}
struct Deck {
    Mixer* m; SRWLOCK producer=SRWLOCK_INIT;
    std::vector<Frame> pcm;
    std::atomic<uint64_t> read{0}, write{0}, gen{1}, played{0};
    std::atomic<int> state{Idle};
    std::atomic<bool> eos{false}, paused{false}, mute{false};
    std::atomic<float> amp{1};
    std::atomic<double> anchor{NAN}, expect_flush{0}, lag{0}, render_time{0}, latency{.15};
    std::atomic<bool> lag_valid{false}, sounding{false};
    std::atomic<unsigned> block_size{480}, anchor_serial{0}, vari_serial{0};
    std::atomic<double> vari_target{1}, vari_seconds{0};
    std::atomic<Deck*> prev{nullptr}, next{nullptr};
    std::atomic<uint64_t> starvation{0};
    // Following fields belong exclusively to the device thread.
    uint64_t last_gen=1, block=0, vari_pos=0, vari_n=0;
    unsigned last_anchor=0,last_vari=0;
    float gain=0; double rate=1,phase=0,r0=1,r1=1;
    bool varying=false;
    explicit Deck(Mixer* owner):m(owner),pcm(CAP){}
};
struct Mixer {
    using Clock=int64_t(*)(); Clock clock;
    Deck* decks[8]{}; std::atomic<unsigned> count{0};
    std::atomic<bool> closed{false}; std::atomic<double> last_render{0};
    std::atomic<uint64_t> callbacks{0}, underruns{0}, vis_pos{0};
    std::atomic<float> visual[VIS];
    std::atomic<float> matrix[4]; float current[4]{1,0,0,1};
    // Look-ahead limiter (device thread only): peaks above the ceiling are pulled down smoothly
    // instead of being clipped, which on boosted bass sounded like a buzz.
    static constexpr unsigned LA=72;              // 1.5 ms at 48 kHz
    float la[LA*2]{}; unsigned la_pos=0, hold=0; float g_hold=1, g=1;
    std::atomic<Deck*> fade_from{nullptr}, fade_to{nullptr};
    std::atomic<uint64_t> fade_duration{0}, fade_pos{0};
    explicit Mixer(Clock fn):clock(fn) {
        for(auto& v:visual) v.store(0);
        for(unsigned i=0;i<4;++i) matrix[i].store(current[i]);
    }
    ~Mixer(){for(unsigned i=0;i<count.load();++i) delete decks[i];}
};

static void stale(Deck* d,uint64_t generation) {
    uint64_t r=d->read.load(std::memory_order_relaxed),w=d->write.load(std::memory_order_acquire);
    while(r<w && d->pcm[r&MASK].gen!=generation) ++r;
    d->read.store(r,std::memory_order_release);
}
static uint64_t queued(Deck* d) {return d->write.load(std::memory_order_acquire)-d->read.load(std::memory_order_acquire);}
static void cancel_fade(Deck* d) {
    Mixer* m=d->m;
    if(m->fade_from.load()==d || m->fade_to.load()==d) {
        m->fade_duration.store(0);m->fade_from.store(nullptr);m->fade_to.store(nullptr);
    }
}
API void* na_create(void* clock) {
    if(!clock)return nullptr;
    try{return new Mixer(reinterpret_cast<Mixer::Clock>(clock));}catch(...){return nullptr;}
}
API void na_close(void* ptr) {if(ptr)static_cast<Mixer*>(ptr)->closed.store(true);}
API void na_destroy(void* ptr) {delete static_cast<Mixer*>(ptr);}
API void* na_deck(void* ptr) {
    auto* m=static_cast<Mixer*>(ptr); unsigned n=m->count.load(); if(n>=8)return nullptr;
    try {auto* d=new Deck(m);m->decks[n]=d;m->count.store(n+1,std::memory_order_release);return d;}catch(...){return nullptr;}
}
API void na_reset(void* ptr) {
    auto* d=static_cast<Deck*>(ptr);
    AcquireSRWLockExclusive(&d->producer);
    d->gen.fetch_add(1);d->state.store(Idle);d->eos.store(false);d->paused.store(false);
    d->anchor.store(NAN);d->played.store(0);d->anchor_serial.fetch_add(1);
    d->lag_valid.store(false);d->expect_flush.store(0);d->vari_seconds.store(0);
    d->vari_target.store(1);d->vari_serial.fetch_add(1);
    ReleaseSRWLockExclusive(&d->producer);
    Deck* p=d->prev.exchange(nullptr);Deck* n=d->next.exchange(nullptr);
    if(p)p->next.store(nullptr);
    if(n){n->prev.store(nullptr);if(n->state.load()==Chained)n->state.store(Sync);}
    cancel_fade(d);
}
API void na_anchor(void* ptr,double ms,int expect) {
    auto* d=static_cast<Deck*>(ptr);d->anchor.store(ms);d->played.store(0);
    d->expect_flush.store(expect?now_s()+1.5:0);d->anchor_serial.fetch_add(1);
}
API void na_gain(void* ptr,float amp,int mute) {
    auto* d=static_cast<Deck*>(ptr);d->amp.store(std::max(0.f,amp));d->mute.store(mute!=0);
}
API void na_vari(void* ptr,double target,double seconds) {
    auto* d=static_cast<Deck*>(ptr);d->vari_target.store(std::clamp(target,0.,1.));
    d->vari_seconds.store(std::max(0.,seconds));d->vari_serial.fetch_add(1,std::memory_order_release);
}
API int na_vari_busy(void* ptr) {
    auto* d=static_cast<Deck*>(ptr);return d->vari_seconds.load()>0;
}
API void na_chain(void* first,void* second) {
    auto* a=static_cast<Deck*>(first);auto* b=static_cast<Deck*>(second);
    b->prev.store(a);b->state.store(Chained);a->next.store(b);
}
API void na_crossfade(void* ptr,void* first,void* second,double seconds) {
    auto* m=static_cast<Mixer*>(ptr);m->fade_pos.store(0);
    m->fade_from.store(static_cast<Deck*>(first));m->fade_to.store(static_cast<Deck*>(second));
    m->fade_duration.store(std::max(uint64_t(1),uint64_t(seconds*SR)),std::memory_order_release);
}
API int na_fade_done(void* ptr) {
    auto* m=static_cast<Mixer*>(ptr);auto n=m->fade_duration.load();return n && m->fade_pos.load()>=n;
}

template<typename T> static inline float sample_of(T v);
template<> inline float sample_of<int16_t>(int16_t v){return v/32768.f;}
template<> inline float sample_of<float>(float v){return std::isfinite(v)?v:0.f;}
template<typename T> static void play_samples(void* ptr,const void* samples,unsigned count,int64_t pts) {
    auto* d=static_cast<Deck*>(ptr);auto* input=static_cast<const T*>(samples);
    uint64_t generation=d->gen.load();unsigned at=0;
    while(at<count && !d->m->closed.load() && d->gen.load()==generation) {
        AcquireSRWLockExclusive(&d->producer);
        if(d->gen.load()!=generation){ReleaseSRWLockExclusive(&d->producer);return;}
        uint64_t w=d->write.load(std::memory_order_relaxed),r=d->read.load(std::memory_order_acquire);
        unsigned n=std::min(uint64_t(count-at),CAP-(w-r));
        for(unsigned i=0;i<n;++i) {
            auto& f=d->pcm[(w+i)&MASK];f.l=sample_of<T>(input[(at+i)*2]);f.r=sample_of<T>(input[(at+i)*2+1]);
            f.pts=pts+int64_t((at+i)*1000000.0/SR);f.gen=generation;
        }
        d->write.store(w+n,std::memory_order_release);
        if(n && (d->state.load()==Idle || d->state.load()==Done))
            d->state.store(d->prev.load()?Chained:Sync);
        ReleaseSRWLockExclusive(&d->producer);
        at+=n;if(!n)Sleep(1);  // decoder may wait; device callback never does
    }
}
// S16 kept for compatibility; FL32 is preferred: VLC's equalizer / bass boost can exceed 0 dBFS, and the
// integer conversion inside VLC clipped those peaks hard before they ever reached our limiter.
API void na_play(void* ptr,const void* samples,unsigned count,int64_t pts) {play_samples<int16_t>(ptr,samples,count,pts);}
API void na_play_f32(void* ptr,const void* samples,unsigned count,int64_t pts) {play_samples<float>(ptr,samples,count,pts);}
API void na_pause(void* ptr,int64_t) {static_cast<Deck*>(ptr)->paused.store(true);}
API void na_resume(void* ptr,int64_t) {static_cast<Deck*>(ptr)->paused.store(false);}
API void na_flush(void* ptr,int64_t) {
    auto* d=static_cast<Deck*>(ptr);AcquireSRWLockExclusive(&d->producer);
    d->gen.fetch_add(1);d->played.store(0);d->eos.store(false);d->lag_valid.store(false);
    if(now_s()>=d->expect_flush.load())d->anchor.store(NAN);
    if(d->state.load()==Play)d->state.store(Sync);
    ReleaseSRWLockExclusive(&d->producer);
}
API void na_drain(void* ptr) {
    auto* d=static_cast<Deck*>(ptr);auto generation=d->gen.load();d->eos.store(true);
    if(!queued(d) && d->state.load()==Idle)d->state.store(Done);
    while(!d->m->closed.load() && d->gen.load()==generation && d->state.load()!=Done) {
        Sleep(5);
    }
}
API void na_volume(void*,float,bool) {} // gain is owned by our output, not VLC

static float envelope(Mixer* m,Deck* d,uint64_t at,bool running) {
    uint64_t duration=m->fade_duration.load();if(!duration)return 1;
    bool incoming=m->fade_to.load()==d,outgoing=m->fade_from.load()==d;
    if(!incoming && !outgoing)return 1;
    double t=std::min(1.,double(m->fade_pos.load()+(running?at:0))/double(duration));
    return float(incoming?std::sin(t*1.5707963267948966):std::cos(t*1.5707963267948966));
}
static Deck* render_deck(Deck* d,float* out,unsigned n,unsigned& pos,int64_t dac,uint64_t block,bool handoff,bool fade_running) {
    if(d->block==block)return nullptr;d->block=block;
    d->sounding.store(false);
    uint64_t generation=d->gen.load();stale(d,generation);
    if(d->last_gen!=generation){d->played.store(0);d->last_gen=generation;d->varying=false;d->rate=1;d->phase=0;}
    unsigned anchor=d->anchor_serial.load();if(anchor!=d->last_anchor){d->played.store(0);d->last_anchor=anchor;}
    unsigned vari=d->vari_serial.load(std::memory_order_acquire);
    if(vari!=d->last_vari) {
        d->last_vari=vari;d->r0=d->rate;d->r1=d->vari_target.load();
        if(d->r1>.5 && !d->varying && d->rate>=.999)d->r0=0;
        d->vari_n=uint64_t(d->vari_seconds.load()*SR);d->vari_pos=0;d->phase=0;
        d->varying=d->vari_n>0;if(!d->varying)d->rate=d->r1;
    }
    int state=d->state.load();
    if(handoff){d->prev.store(nullptr);state=Play;d->state.store(Play);d->gain=d->mute.load()?0:d->amp.load();}
    if(state==IdlePlay && queued(d)){state=Play;d->state.store(Play);}
    if(state==Idle || state==Done || state==Chained || (d->paused.load() && !d->varying))return nullptr;
    uint64_t r=d->read.load(std::memory_order_relaxed),w=d->write.load(std::memory_order_acquire);
    if(state==Sync) {
        if(r==w)return nullptr;
        double delta=double(d->pcm[r&MASK].pts-dac)*SR/1000000.;
        if(delta>=n)return nullptr;
        if(delta>0)pos=unsigned(delta);
        // Late system scheduling must never discard the start of the song.
        state=Play;d->state.store(Play);
    }
    uint64_t consumed=0;bool sounding=false;
    float target=d->mute.load()?0:d->amp.load(),start_gain=d->gain;
    while(pos<n && r<w) {
        auto& f=d->pcm[r&MASK];if(f.gen!=generation)break;
        if(!sounding){d->lag.store(double(dac+int64_t(pos*1000000.0/SR)-f.pts)/1000000.);d->lag_valid.store(true);}
        double rate=1;
        if(d->varying) {
            double t=std::min(1.,double(d->vari_pos)/std::max(uint64_t(1),d->vari_n));
            rate=d->r0+(d->r1-d->r0)*t*t*(3-2*t);d->rate=rate;
        } else if(d->paused.load())break;
        float l=f.l,rr=f.r;
        if(d->varying && r+1<w) {
            const auto& b=d->pcm[(r+1)&MASK];
            if(b.gen==generation){l+=float(d->phase)*(b.l-l);rr+=float(d->phase)*(b.r-rr);}
        }
        float gain=start_gain+(target-start_gain)*std::min(1.f,float(pos+1)/480.f);
        if(d->varying)gain*=float(std::clamp(rate/.28,0.,1.));
        gain*=envelope(d->m,d,pos,fade_running);
        out[pos*2]+=l*gain;out[pos*2+1]+=rr*gain;sounding=true;++pos;
        d->phase+=rate;unsigned advance=unsigned(d->phase);d->phase-=advance;
        advance=unsigned(std::min(uint64_t(advance),w-r));r+=advance;consumed+=advance;
        if(d->varying && ++d->vari_pos>=d->vari_n) {
            d->varying=false;d->rate=d->r1;d->vari_seconds.store(0);
        }
        w=d->write.load(std::memory_order_acquire);
    }
    d->read.store(r,std::memory_order_release);d->gain=target;d->played.fetch_add(consumed);
    d->sounding.store(sounding && !d->varying);
    if(pos<n && r==w && !d->eos.load() && !d->paused.load())d->starvation.fetch_add(1);
    if(r==w && d->eos.load() && d->gen.load()==generation) {
        d->state.store(Done);return d->next.exchange(nullptr);
    }
    return nullptr;
}
API void na_render(void* ptr,float* out,unsigned n,double delay,unsigned long flags) {
    auto* m=static_cast<Mixer*>(ptr);std::memset(out,0,n*2*sizeof(float));if(m->closed.load())return;
    double time=now_s();m->last_render.store(time);auto block=m->callbacks.fetch_add(1)+1;
    if(flags&4)m->underruns.fetch_add(1); // paOutputUnderflow
    int64_t dac=m->clock()+int64_t(delay*1000000.);
    auto* incoming=m->fade_to.load();bool fade_running=false;
    if(incoming && queued(incoming) && !incoming->paused.load()) {
        stale(incoming,incoming->gen.load());
        auto r=incoming->read.load(),w=incoming->write.load();
        fade_running=r<w && incoming->pcm[r&MASK].pts<=dac;
    }
    for(unsigned i=0;i<m->count.load(std::memory_order_acquire);++i) {
        auto* d=m->decks[i];unsigned at=0;
        for(unsigned hops=0;d && hops<8;++hops) {
            d->render_time.store(time);d->latency.store(delay);d->block_size.store(n);
            d=render_deck(d,out,n,at,dac,block,hops!=0,fade_running);
        }
    }
    if(fade_running)m->fade_pos.fetch_add(n);
    float target[4];for(unsigned j=0;j<4;++j)target[j]=m->matrix[j].load();
    uint64_t vis=m->vis_pos.load(std::memory_order_relaxed);
    constexpr float CEIL=.94f;
    const float attack=1.f-std::exp(-5.f/Mixer::LA), release=1.f-std::exp(-1.f/(.15f*SR));
    for(unsigned i=0;i<n;++i) {
        for(unsigned j=0;j<4;++j)m->current[j]+=(target[j]-m->current[j])*.0020811647f;
        float l=out[i*2],r=out[i*2+1];
        float L=l*m->current[0]+r*m->current[1],R=l*m->current[2]+r*m->current[3];
        // limiter: the gain needed for the incoming sample is held for the look-ahead window and reached
        // before that sample leaves the delay line; release is slow (150 ms) so it does not pump or buzz
        float peak=std::max(std::abs(L),std::abs(R));
        float need=peak>CEIL?CEIL/peak:1.f;
        if(need<=m->g_hold){m->g_hold=need;m->hold=Mixer::LA;}
        else if(m->hold)--m->hold;
        else m->g_hold=need;
        m->g+=(m->g_hold-m->g)*(m->g_hold<m->g?attack:release);
        unsigned k=m->la_pos*2;
        float dl=m->la[k],dr=m->la[k+1];m->la[k]=L;m->la[k+1]=R;
        m->la_pos=(m->la_pos+1)%Mixer::LA;
        out[i*2]=dl*m->g;out[i*2+1]=dr*m->g;
        for(unsigned ch=0;ch<2;++ch) {                // last-resort safety, practically never reached
            float& x=out[i*2+ch];float a=std::abs(x);
            if(a>.985f)x=std::copysign(.985f+.015f*std::tanh((a-.985f)/.015f),x);
        }
        m->visual[(vis+i)&(VIS-1)].store((out[i*2]+out[i*2+1])*.5f,std::memory_order_relaxed);
    }
    m->vis_pos.store(vis+n,std::memory_order_release);
}
struct PaTime {double inputBufferAdcTime,currentTime,outputBufferDacTime;};
API int na_pa_callback(const void*,void* output,unsigned long n,const PaTime* t,unsigned long flags,void* opaque) {
    double delay=t?t->outputBufferDacTime-t->currentTime:.2;
    if(!(delay>0 && delay<2))delay=.2;
    na_render(opaque,static_cast<float*>(output),unsigned(n),delay,flags);return 0;
}
API void na_info(void* ptr,double* out) {
    auto* d=static_cast<Deck*>(ptr);
    out[0]=d->anchor.load();out[1]=double(d->played.load());out[2]=d->render_time.load();
    out[3]=d->sounding.load()&&!d->paused.load();out[4]=d->latency.load();out[5]=d->block_size.load();
    out[6]=d->state.load();out[7]=d->lag_valid.load()?d->lag.load():0;out[8]=double(queued(d));out[9]=double(d->starvation.load());
}
API void na_stats(void* ptr,double* out) {
    auto* m=static_cast<Mixer*>(ptr);out[0]=m->last_render.load();out[1]=double(m->callbacks.load());out[2]=double(m->underruns.load());
}
API void na_matrix(void* ptr,const float* matrix) {
    auto* m=static_cast<Mixer*>(ptr);for(unsigned i=0;i<4;++i)m->matrix[i].store(matrix[i]);
}
API unsigned na_visual(void* ptr,float* out,unsigned n,unsigned delay) {
    auto* m=static_cast<Mixer*>(ptr);uint64_t pos=m->vis_pos.load(std::memory_order_acquire);
    n=std::min(n,VIS/2);delay=std::min(delay,VIS/2-n);if(pos<n+delay)return 0;
    for(unsigned i=0;i<n;++i)out[i]=m->visual[(pos-delay-n+i)&(VIS-1)].load(std::memory_order_relaxed);
    return n;
}
