cmake_minimum_required(VERSION 3.20)
project(lodziarz_native CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# /MD — libfbxsdk-md.lib wymaga dynamicznego CRT; vcruntime dostarcza PyInstaller

# --- meshoptimizer.dll (ctypes binding po stronie Pythona) ---
set(MESHOPT_BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
set(MESHOPT_INSTALL OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../third_party/meshoptimizer-0.25 meshopt)

# --- fbx_writer.exe (Autodesk FBX SDK — to samo podejscie co Brutgen) ---
if(NOT FBXSDK_ROOT)
  set(FBXSDK_ROOT "G:/Programing/FBXSDK/2020.3.4")
endif()
if(NOT EXISTS ${FBXSDK_ROOT}/include/fbxsdk.h)
  message(FATAL_ERROR "FBX SDK nie znaleziony: ${FBXSDK_ROOT}")
endif()

add_executable(fbx_writer fbx_writer.cpp)
target_include_directories(fbx_writer PRIVATE ${FBXSDK_ROOT}/include)
# SDK 2020.3.x na Windows: lib/vs2022/x64/release; starsze: lib/x64/release
if(EXISTS ${FBXSDK_ROOT}/lib/vs2022/x64/release)
  set(FBXSDK_LIBDIR ${FBXSDK_ROOT}/lib/vs2022/x64/release)
else()
  set(FBXSDK_LIBDIR ${FBXSDK_ROOT}/lib/x64/release)
endif()
target_link_libraries(fbx_writer PRIVATE
  ${FBXSDK_LIBDIR}/libfbxsdk-md.lib
  ${FBXSDK_LIBDIR}/libxml2-md.lib
  ${FBXSDK_LIBDIR}/zlib-md.lib)

# wyjscie do lodziarz/bin — Python znajduje binarki obok pakietu
set_target_properties(fbx_writer meshoptimizer PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../lodziarz/bin
  RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../lodziarz/bin)
