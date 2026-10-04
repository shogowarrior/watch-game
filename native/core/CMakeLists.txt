# ESP-IDF component for native/idf. PlatformIO's Arduino build reads library.json instead.
file(GLOB srcs "${CMAKE_CURRENT_LIST_DIR}/src/*.cpp")
idf_component_register(SRCS ${srcs} INCLUDE_DIRS "include")
