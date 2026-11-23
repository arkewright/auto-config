find_package(LLVM CONFIG REQUIRED)
find_package(Clang CONFIG REQUIRED)

add_library(kcfg_frontend MODULE src/Instrument.cpp)
set_target_properties(kcfg_frontend PROPERTIES PREFIX "")
target_include_directories(kcfg_frontend SYSTEM PRIVATE ${CLANG_INCLUDE_DIRS} ${LLVM_INCLUDE_DIRS})
target_link_libraries(kcfg_frontend PRIVATE clang-cpp LLVM)
target_compile_options(kcfg_frontend PRIVATE -Wall -Wextra -Werror=return-type)
if(NOT LLVM_ENABLE_RTTI)
  target_compile_options(kcfg_frontend PRIVATE -fno-rtti)
endif()

if(BUILD_TESTING)
  add_test(NAME frontend-instrumentation COMMAND bash
    ${CMAKE_CURRENT_SOURCE_DIR}/test/check.sh
    $<TARGET_FILE:clang> $<TARGET_FILE:kcfg_frontend>)
endif()
