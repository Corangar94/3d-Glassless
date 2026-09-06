# Hash deployed files only after every copy succeeded. Atomic same-directory publish.
if(NOT DEFINED ROOT OR NOT DEFINED SOURCE_COMMIT)
    message(FATAL_ERROR "ROOT and SOURCE_COMMIT required")
endif()
file(SHA256 "${ROOT}/Glassless3DOverlay.exe" EXE_HASH)
file(SHA256 "${ROOT}/onnxruntime.dll" ORT_HASH)
file(SHA256 "${ROOT}/DirectML.dll" DML_HASH)
file(WRITE "${ROOT}/Glassless3DOverlay.build.json.tmp"
    "{\n  \"source_commit\": \"${SOURCE_COMMIT}\",\n  \"compiler\": \"${COMPILER}\",\n  \"files\": {\n    \"Glassless3DOverlay.exe\": \"${EXE_HASH}\",\n    \"onnxruntime.dll\": \"${ORT_HASH}\",\n    \"DirectML.dll\": \"${DML_HASH}\"\n  }\n}\n")
file(RENAME "${ROOT}/Glassless3DOverlay.build.json.tmp" "${ROOT}/Glassless3DOverlay.build.json")
