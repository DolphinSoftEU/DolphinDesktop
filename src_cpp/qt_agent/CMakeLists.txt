cmake_minimum_required(VERSION 3.18)
project(dolphin_qt_agent LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Build scripts select a major version explicitly so the same checkout can
# reproduce both shipped ABIs without depending on package search order.
set(DOLPHIN_QT_MAJOR "" CACHE STRING "Qt major version to build (5 or 6)")
if(DOLPHIN_QT_MAJOR STREQUAL "5")
    find_package(Qt5 REQUIRED COMPONENTS Core Widgets)
    set(QT_VERSION_MAJOR 5)
elseif(DOLPHIN_QT_MAJOR STREQUAL "6")
    find_package(Qt6 REQUIRED COMPONENTS Core Widgets)
    set(QT_VERSION_MAJOR 6)
else()
    find_package(QT NAMES Qt6 Qt5 REQUIRED COMPONENTS Core Widgets)
endif()
find_package(Qt${QT_VERSION_MAJOR} REQUIRED COMPONENTS Core Widgets Network)

# Qml / Quick are optional — only needed when we want QML introspection.
find_package(Qt${QT_VERSION_MAJOR} OPTIONAL_COMPONENTS Qml Quick)

set(AGENT_NAME "dolphin_qt${QT_VERSION_MAJOR}_agent")

set(AGENT_SOURCES
    agent_entry.cpp
    object_walker.cpp
    pipe_server.cpp
    qml_walker.cpp
    graphics_walker.cpp
)

add_library(${AGENT_NAME} SHARED ${AGENT_SOURCES})

target_link_libraries(${AGENT_NAME} PRIVATE
    Qt${QT_VERSION_MAJOR}::Core
    Qt${QT_VERSION_MAJOR}::Widgets
    Qt${QT_VERSION_MAJOR}::Network
)

if(Qt${QT_VERSION_MAJOR}Qml_FOUND AND Qt${QT_VERSION_MAJOR}Quick_FOUND)
    target_link_libraries(${AGENT_NAME} PRIVATE
        Qt${QT_VERSION_MAJOR}::Qml
        Qt${QT_VERSION_MAJOR}::Quick
    )
    target_compile_definitions(${AGENT_NAME} PRIVATE DOLPHIN_QT_AGENT_HAS_QML=1)
    message(STATUS "QML support: ENABLED (Qml + Quick found)")
else()
    message(STATUS "QML support: DISABLED (Qml or Quick not found)")
endif()

if(WIN32)
    target_link_libraries(${AGENT_NAME} PRIVATE kernel32 user32 advapi32)
    # Reduce DLL bloat; we don't need C++ exceptions across DLL boundary.
    target_compile_definitions(${AGENT_NAME} PRIVATE
        WIN32_LEAN_AND_MEAN=1
        NOMINMAX=1
    )
    if(MSVC)
        target_compile_options(${AGENT_NAME} PRIVATE /Brepro)
        target_link_options(${AGENT_NAME} PRIVATE /Brepro)
    endif()
endif()

# Output directly into the Python package so the wheel build can pick it up.
set(DOLPHIN_AGENT_OUTPUT_DIR
    "${CMAKE_SOURCE_DIR}/../../src/dolphin_desktop/_qt_agent"
    CACHE PATH "Directory that receives the built agent DLL")
set_target_properties(${AGENT_NAME} PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${DOLPHIN_AGENT_OUTPUT_DIR}"
    LIBRARY_OUTPUT_DIRECTORY "${DOLPHIN_AGENT_OUTPUT_DIR}"
    # All build configs land in the same target dir (no Debug/Release split).
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${DOLPHIN_AGENT_OUTPUT_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_RELEASE "${DOLPHIN_AGENT_OUTPUT_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${DOLPHIN_AGENT_OUTPUT_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_DEBUG "${DOLPHIN_AGENT_OUTPUT_DIR}"
)

message(STATUS "Agent target:  ${AGENT_NAME}")
message(STATUS "Qt version:    ${QT_VERSION_MAJOR}")
message(STATUS "Output dir:    ${DOLPHIN_AGENT_OUTPUT_DIR}")
