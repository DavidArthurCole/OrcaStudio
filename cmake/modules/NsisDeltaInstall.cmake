set(_stage "${CPACK_TEMPORARY_DIRECTORY}")
set(_manifest_name "install_manifest.txt")
set(_file_list "${CPACK_TOPLEVEL_DIRECTORY}/orca_delta_files.txt")

file(GLOB_RECURSE _files LIST_DIRECTORIES false RELATIVE "${_stage}" "${_stage}/*")
list(SORT _files)
list(REMOVE_ITEM _files "${_manifest_name}")

set(_manifest "")
set(_names "")
set(_commands "")
set(_index 0)

foreach(_rel IN LISTS _files)
    file(SHA256 "${_stage}/${_rel}" _hash)
    string(REPLACE "/" "\\" _win "${_rel}")
    string(APPEND _manifest "${_win}|${_hash}\n")
    string(APPEND _names "${_win}\n")
    get_filename_component(_dir "${_win}" DIRECTORY)
    if(_dir STREQUAL "")
        set(_out "$INSTDIR")
    else()
        set(_out "$INSTDIR\\${_dir}")
    endif()
    string(APPEND _commands "  !insertmacro OrcaDeltaFile ${_index} \"${_out}\" \"${_win}\"\n")
    math(EXPR _index "${_index} + 1")
endforeach()

file(WRITE "${_stage}/${_manifest_name}" "${_manifest}")
file(WRITE "${_file_list}" "${_names}")
file(TO_NATIVE_PATH "${_file_list}" _file_list)
file(TO_NATIVE_PATH "${CPACK_ORCA_DELTA_DIR}/NsisDeltaInstall.ps1" _delta_script)

set(CPACK_NSIS_FULL_INSTALL
"  SetOverwrite on
  !insertmacro OrcaDeltaPrepare \"${_delta_script}\" \"${_file_list}\"
${_commands}  SetOutPath \"$INSTDIR\"
  File \"\${INST_DIR}\\${_manifest_name}\"
")
