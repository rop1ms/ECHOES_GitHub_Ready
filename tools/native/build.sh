#!/bin/sh
# Сборка echoes_fx.dll (MSYS2 UCRT64 GCC). Две версии: обычная (x86-64-v2, SSE4.2 — любой ПК
# с 2009 г.) и echoes_fx_avx2.dll (AVX2+FMA). native_fx.py сам выбирает подходящую.
# Всё статически: DLL зависит только от системных библиотек Windows. Без исключений и без
# winpthreads (см. nothrow_stubs.cpp): её очистка вешала выход процесса.
set -e
cd "$(dirname "$0")"
export PATH=/c/msys64/ucrt64/bin:$PATH
SRC="fx_common.cpp fluid.cpp milk.cpp glow.cpp nebula.cpp blur.cpp sprite.cpp viz.cpp clipfx.cpp body.cpp studio.cpp nothrow_stubs.cpp"
OUT=../../app
FLAGS="-O3 -ffast-math -fno-math-errno -fno-exceptions -fno-threadsafe-statics --param vect-max-version-for-alias-checks=100 -std=c++17 -shared -static -static-libgcc -static-libstdc++ -s -DNDEBUG -Wall -Wno-unused-function"
g++ $FLAGS -march=x86-64-v2 -mtune=generic -o $OUT/echoes_fx.dll $SRC
g++ $FLAGS -march=x86-64-v3 -mtune=znver2 -o $OUT/echoes_fx_avx2.dll $SRC
ls -la $OUT/echoes_fx*.dll
