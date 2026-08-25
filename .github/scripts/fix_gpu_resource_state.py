from pathlib import Path

path = Path("overlay/depth_infer.cpp")
text = path.read_text(encoding="utf-8")
old = '''            fixed.input_resource.Attach(input_resource);
            fixed.output_resource.Attach(output_resource);
            const std::array<int64_t, 4> input_shape = {
'''
new = '''            fixed.input_resource.Attach(input_resource);
            fixed.output_resource.Attach(output_resource);
            // DirectML provider allocations are exposed for unordered-access
            // execution. Track that initial state so the very first upload
            // emits UAV -> COPY_DEST -> UAV barriers rather than depending on
            // implicit COMMON-state promotion.
            fixed.input_in_uav_state = true;
            const std::array<int64_t, 4> input_shape = {
'''
if new not in text:
    if text.count(old) != 1:
        raise RuntimeError("DML allocation attachment block changed unexpectedly")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8", newline="\n")
