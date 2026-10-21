# SCons only passes -f* build flags to the compiler; the sanitizer runtime
# must be linked too, or the native_asan test binary fails to link.
Import("env")  # noqa: F821 - provided by PlatformIO

env.Append(LINKFLAGS=["-fsanitize=address,undefined"])  # noqa: F821
