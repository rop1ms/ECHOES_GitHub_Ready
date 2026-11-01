// QA-only native thread used to prove callbacks progress while Python holds GIL.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
struct Time {double adc,current,dac;};
using Callback=int(*)(const void*,void*,unsigned long,const Time*,unsigned long,void*);
struct Probe {Callback fn;void* mixer;float* samples;unsigned blocks;HANDLE thread;};
static DWORD WINAPI pump(void* data) {
    auto* p=static_cast<Probe*>(data);
    HANDLE timer=CreateWaitableTimerExW(nullptr,nullptr,2,TIMER_ALL_ACCESS);
    if(!timer)timer=CreateWaitableTimerW(nullptr,FALSE,nullptr);
    LARGE_INTEGER freq,start,now;QueryPerformanceFrequency(&freq);QueryPerformanceCounter(&start);
    for(unsigned i=0;i<p->blocks;++i) {
        QueryPerformanceCounter(&now);double t=double(now.QuadPart)/freq.QuadPart;
        Time info{0,t,t+.25};p->fn(nullptr,p->samples+i*960,480,&info,0,p->mixer);
        QueryPerformanceCounter(&now);
        double remaining=(i+1)*.01-double(now.QuadPart-start.QuadPart)/freq.QuadPart;
        if(remaining>0) {LARGE_INTEGER due;due.QuadPart=-LONGLONG(remaining*1e7);SetWaitableTimer(timer,&due,0,nullptr,nullptr,FALSE);WaitForSingleObject(timer,INFINITE);}
    }
    CloseHandle(timer);return 0;
}
extern "C" __declspec(dllexport) void* probe_start(void* fn,void* mixer,float* samples,unsigned blocks) {
    auto* p=new Probe{reinterpret_cast<Callback>(fn),mixer,samples,blocks,nullptr};
    p->thread=CreateThread(nullptr,0,pump,p,0,nullptr);return p;
}
extern "C" __declspec(dllexport) int probe_finish(void* ptr) {
    auto* p=static_cast<Probe*>(ptr);DWORD result=WaitForSingleObject(p->thread,10000);
    CloseHandle(p->thread);delete p;return result==WAIT_OBJECT_0;
}
