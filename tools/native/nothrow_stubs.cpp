// nothrow_stubs.cpp — библиотека собирается без исключений (-fno-exceptions). Здесь — свои
// определения функций, которыми libstdc++ «бросает» ошибки: так в DLL не попадают пул исключений
// libstdc++ и вместе с ним winpthreads, чья очистка при выходе процесса могла повесить ExitProcess.
// Ошибки такого рода (нехватка памяти, выход за границы) — фатальны: процесс завершается.
#include <bits/functexcept.h>

#include <cstdlib>

namespace std {
void __throw_bad_exception() { abort(); }
void __throw_bad_alloc() { abort(); }
void __throw_bad_array_new_length() { abort(); }
void __throw_bad_cast() { abort(); }
void __throw_bad_typeid() { abort(); }
void __throw_logic_error(const char*) { abort(); }
void __throw_domain_error(const char*) { abort(); }
void __throw_invalid_argument(const char*) { abort(); }
void __throw_length_error(const char*) { abort(); }
void __throw_out_of_range(const char*) { abort(); }
void __throw_out_of_range_fmt(const char*, ...) { abort(); }
void __throw_runtime_error(const char*) { abort(); }
void __throw_range_error(const char*) { abort(); }
void __throw_overflow_error(const char*) { abort(); }
void __throw_underflow_error(const char*) { abort(); }
void __throw_system_error(int) { abort(); }
void __throw_bad_function_call() { abort(); }
}  // namespace std

// свои operator new/delete: версия из libstdc++ бросает bad_alloc и тянет за собой механизм исключений
#include <new>

void* operator new(std::size_t n) {
    void* p = std::malloc(n ? n : 1);
    if (!p) std::abort();
    return p;
}
void* operator new[](std::size_t n) { return ::operator new(n); }
void* operator new(std::size_t n, const std::nothrow_t&) noexcept { return std::malloc(n ? n : 1); }
void* operator new[](std::size_t n, const std::nothrow_t&) noexcept { return std::malloc(n ? n : 1); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
void operator delete(void* p, const std::nothrow_t&) noexcept { std::free(p); }
void operator delete[](void* p, const std::nothrow_t&) noexcept { std::free(p); }
