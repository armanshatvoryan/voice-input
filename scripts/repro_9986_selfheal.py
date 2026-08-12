"""Integration: MicStream self-heals when the default input device vanishes.

Mirrors the production wedge exactly:
  boot with a (soon-to-die) default input -> device vanishes -> next take.
Without the fix, step 4 dies with PaErrorCode -9986; with it, the take opens
on the restored default. Uses a private aggregate wrapping the built-in mic;
the system default input is switched to it for ~2s and restored automatically
when it is destroyed.
"""
import ctypes
import struct
import sys
import time

sys.path.insert(0, __import__("os").path.expanduser("~/voice-input"))

core = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")


def fourcc(code: bytes) -> int:
    return struct.unpack(">I", code)[0]


class PropertyAddress(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32),
                ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


SYSTEM_OBJECT = 1
GLOBAL = fourcc(b"glob")
DEFAULT_INPUT = fourcc(b"dIn ")

core.AudioObjectGetPropertyData.argtypes = [
    ctypes.c_uint32, ctypes.POINTER(PropertyAddress), ctypes.c_uint32,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
core.AudioObjectSetPropertyData.argtypes = [
    ctypes.c_uint32, ctypes.POINTER(PropertyAddress), ctypes.c_uint32,
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
core.AudioHardwareCreateAggregateDevice.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
core.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]


def get_default_input() -> int:
    addr = PropertyAddress(DEFAULT_INPUT, GLOBAL, 0)
    dev = ctypes.c_uint32(0)
    size = ctypes.c_uint32(4)
    assert core.AudioObjectGetPropertyData(
        SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size),
        ctypes.byref(dev)) == 0
    return dev.value


def set_default_input(dev: int) -> None:
    addr = PropertyAddress(DEFAULT_INPUT, GLOBAL, 0)
    val = ctypes.c_uint32(dev)
    assert core.AudioObjectSetPropertyData(
        SYSTEM_OBJECT, ctypes.byref(addr), 0, None, 4, ctypes.byref(val)) == 0


def device_uid(dev: int) -> str:
    cf = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    addr = PropertyAddress(fourcc(b"uid "), GLOBAL, 0)
    ref = ctypes.c_void_p(0)
    size = ctypes.c_uint32(ctypes.sizeof(ref))
    assert core.AudioObjectGetPropertyData(
        dev, ctypes.byref(addr), 0, None, ctypes.byref(size),
        ctypes.byref(ref)) == 0
    buf = ctypes.create_string_buffer(512)
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString(ref, buf, 512, 0x08000100)
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease(ref)
    return buf.value.decode()


def main() -> int:
    from Foundation import NSDictionary
    import objc

    original_default = get_default_input()
    sub_uid = device_uid(original_default)
    print(f"original default input: id={original_default} uid={sub_uid}")

    # NOT private: a private aggregate cannot become the system default input
    # (the set silently no-ops), which would turn this test into a false pass.
    desc = NSDictionary.dictionaryWithDictionary_({
        "uid": "wedge-selfheal-aggregate-uid",
        "name": "wedge-selfheal-aggregate",
        "subdevices": [{"uid": sub_uid}],
    })
    agg = ctypes.c_uint32(0)
    assert core.AudioHardwareCreateAggregateDevice(
        objc.pyobjc_id(desc), ctypes.byref(agg)) == 0
    print(f"aggregate id={agg.value}")
    destroyed = False
    try:
        time.sleep(0.5)
        set_default_input(agg.value)
        for _ in range(20):
            if get_default_input() == agg.value:
                break
            time.sleep(0.1)
        if get_default_input() != agg.value:
            print("ABORT: could not make aggregate the default input — "
                  "test preconditions not met, nothing proven")
            return 2
        print(f"default input now: {get_default_input()} (aggregate)")

        # PortAudio caches the table NOW, with the aggregate as default input
        from voiceinput import audio

        mic = audio.MicStream()          # device=None -> default input
        mic.start()
        time.sleep(0.3)
        first = mic.stop()
        print(f"TAKE 1 on doomed default: OK, {len(first)} frames")

        # the "AirPods disconnect": default input vanishes
        core.AudioHardwareDestroyAggregateDevice(agg.value)
        destroyed = True
        time.sleep(1.0)
        print(f"aggregate destroyed; system default now: {get_default_input()}")

        mic.start()                      # without the fix: -9986 here
        time.sleep(0.3)
        second = mic.stop()
        print(f"TAKE 2 after device vanished: OK, {len(second)} frames")
        print("SELF-HEAL PASS")
        return 0
    except Exception as exc:
        print(f"SELF-HEAL FAIL: {exc!r}")
        return 1
    finally:
        if not destroyed:
            try:
                core.AudioHardwareDestroyAggregateDevice(agg.value)
            except Exception:
                pass
        try:
            if get_default_input() != original_default:
                set_default_input(original_default)
                print(f"default input restored to {original_default}")
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
