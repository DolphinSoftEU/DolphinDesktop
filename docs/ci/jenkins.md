# Jenkins

Run the Jenkins agent under a Windows user account that has an interactive desktop. Avoid running desktop UI tests as `SYSTEM`.

## Declarative Pipeline

```groovy title="Jenkinsfile"
pipeline {
    agent { label 'windows' }

    environment {
        DOLPHIN_TRACE = 'on-failure'
        DOLPHIN_VIDEO = 'keepfailedonly'
    }

    stages {
        stage('Install') {
            steps {
                bat 'pip install "dolphin-desktop[fast]" pytest'
            }
        }

        stage('Doctor') {
            steps {
                bat 'dolphin doctor'
            }
        }

        stage('Test') {
            steps {
                bat 'pytest tests/ -v --dolphin-backend=uia --junitxml=test-results.xml --dolphin-screenshot-on-fail'
            }
            post {
                always {
                    junit 'test-results.xml'
                    archiveArtifacts artifacts: 'dolphin-traces/**,dolphin-videos/**,dolphin-screenshots/**,dolphin-report.html',
                                     allowEmptyArchive: true
                }
            }
        }
    }
}
```

`DOLPHIN_VIDEO` only produces MP4s when an `ffmpeg` binary is on the agent's
`PATH` (or pointed at by `DOLPHIN_FFMPEG`); without it recording is skipped
silently and `dolphin-videos/**` archives nothing. The `fast` extra installs
`mss` for quicker trace screenshots — it does not encode video.

## Agent Checklist

- Windows Server 2019/2022 or Windows 10/11.
- Python 3.11+ in `PATH`.
- Agent process runs as the same Windows user that owns the desktop.
- Test process has the same elevation level as the application under test.

## Headless Smoke Tests

```groovy
stage('Smoke on hidden desktop') {
    steps {
        bat 'dolphin-run pytest tests\\smoke -v --dolphin-backend=uia'
    }
}
```

Use this only for tests verified against the hidden-desktop limitations.
