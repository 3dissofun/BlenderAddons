cmake_minimum_required(VERSION 3.16)
project(ldcompile CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_executable(ldcompile
    src/main.cpp
    src/library.cpp
    src/parser.cpp
    src/texmap.cpp
    src/blob.cpp
)

if(MSVC)
    target_compile_options(ldcompile PRIVATE /W4 /utf-8)
else()
    target_compile_options(ldcompile PRIVATE -Wall -Wextra)
endif()
