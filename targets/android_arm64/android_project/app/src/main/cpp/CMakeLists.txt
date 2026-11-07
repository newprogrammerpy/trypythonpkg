cmake_minimum_required(VERSION 3.22.1)
project(python_runner C)

if(NOT PYTHON_PREFIX_DIR OR NOT PYTHON_VERSION)
    message(FATAL_ERROR "PYTHON_PREFIX_DIR and PYTHON_VERSION are required")
endif()

set(PYTHON_INCLUDE_DIR "${PYTHON_PREFIX_DIR}/include/python${PYTHON_VERSION}")
set(PYTHON_LIBRARY_DIR "${PYTHON_PREFIX_DIR}/lib")
if(NOT EXISTS "${PYTHON_INCLUDE_DIR}/Python.h")
    message(FATAL_ERROR "Python.h is missing from ${PYTHON_INCLUDE_DIR}")
endif()
if(NOT EXISTS "${PYTHON_LIBRARY_DIR}/libpython${PYTHON_VERSION}.so")
    message(FATAL_ERROR "Android libpython is missing from ${PYTHON_LIBRARY_DIR}")
endif()

find_library(ANDROID_LOG_LIBRARY log REQUIRED)
add_library(python_runner SHARED python_runner.c)
target_include_directories(python_runner PRIVATE "${PYTHON_INCLUDE_DIR}")
target_link_directories(python_runner PRIVATE "${PYTHON_LIBRARY_DIR}")
target_link_libraries(
    python_runner
    PRIVATE
    "${ANDROID_LOG_LIBRARY}"
    "python${PYTHON_VERSION}"
)
