from pathlib import Path

path = Path("overlay/depth_infer.cpp")
text = path.read_text(encoding="utf-8")
old_marker = "using Microsoft::WRL::ComPtr;\n\n"
new_marker = '''using Microsoft::WRL::ComPtr;

// The DirectML NuGet header carries the COM interface declaration but MinGW
// does not synthesize __uuidof(IDMLDevice). Keep the official interface IID
// local so DMLCreateDevice links without relying on compiler-specific UUID data.
static const GUID kIID_IDMLDevice = {
    0x6dbd6437, 0x96fd, 0x423f,
    {0xa9, 0x8c, 0xae, 0x5e, 0x7c, 0x2a, 0x57, 0x3f}
};

'''
if new_marker not in text:
    if text.count(old_marker) != 1:
        raise RuntimeError("ComPtr insertion marker changed unexpectedly")
    text = text.replace(old_marker, new_marker, 1)
old_iid = "            __uuidof(IDMLDevice),\n"
new_iid = "            kIID_IDMLDevice,\n"
if new_iid not in text:
    if text.count(old_iid) != 1:
        raise RuntimeError("DMLCreateDevice IID call changed unexpectedly")
    text = text.replace(old_iid, new_iid, 1)
path.write_text(text, encoding="utf-8", newline="\n")
