# TeamCity

Run the TeamCity agent as a logged-in Windows user, not as `SYSTEM`, so UIA can access the desktop.

## Command Line Build Step

```batch
pip install dolphin-desktop pytest
dolphin doctor
pytest tests/ -v --dolphin-backend=uia --tb=short
```

## With Artifacts

```batch
pip install "dolphin-desktop[fast]" pytest
set DOLPHIN_TRACE=on-failure
set DOLPHIN_VIDEO=keepfailedonly
pytest tests/ -v --dolphin-screenshot-on-fail --junitxml=test-results.xml
```

Artifact paths:

```text
dolphin-traces/** => dolphin-traces.zip
dolphin-videos/** => dolphin-videos.zip
dolphin-screenshots/** => dolphin-screenshots.zip
dolphin-report.html
test-results.xml
```

`DOLPHIN_VIDEO` only produces MP4s when an `ffmpeg` binary is on the agent's
`PATH` (or pointed at by `DOLPHIN_FFMPEG`); without it recording is skipped
silently and `dolphin-videos/**` collects nothing. The `fast` extra installs
`mss` for quicker trace screenshots — it does not encode video.

## Kotlin DSL Sketch

```kotlin title=".teamcity/settings.kts"
import jetbrains.buildServer.configs.kotlin.*
import jetbrains.buildServer.configs.kotlin.buildSteps.script

project {
    buildType(DesktopTests)
}

object DesktopTests : BuildType({
    name = "Desktop Tests"

    steps {
        script {
            name = "Install and run"
            scriptContent = """
                pip install dolphin-desktop pytest
                dolphin doctor
                pytest tests/ -v --dolphin-backend=uia --tb=short
            """.trimIndent()
        }
    }

    requirements {
        contains("teamcity.agent.jvm.os.name", "Windows")
    }
})
```

## Headless Smoke Tests

```batch
dolphin-run pytest tests\smoke -v --dolphin-backend=uia
```

Use hidden-desktop execution only for tests that have been checked against the limitations in [Headless Mode](../tutorials/headless-mode.md).
