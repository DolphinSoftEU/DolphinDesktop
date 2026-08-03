import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    objectName: "qmlMainWindow"
    title: "Dolphin QML Demo"
    width: 480
    height: 360
    visible: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        Label {
            id: heading
            objectName: "qmlHeading"
            text: "QML controls"
            font.pixelSize: 18
        }

        TextField {
            id: nameField
            objectName: "qmlNameField"
            Layout.fillWidth: true
            placeholderText: "Type your name..."
            onTextChanged: statusLabel.text = "name=" + text
        }

        RowLayout {
            spacing: 8

            Button {
                id: clickMeBtn
                objectName: "qmlClickButton"
                text: "Click me"
                onClicked: {
                    statusLabel.text = "clicked"
                    statusLabel.accessibleName = "clicked"
                }
            }

            CheckBox {
                id: agreeBox
                objectName: "qmlAgreeCheck"
                text: "Agree"
                onCheckedChanged: statusLabel.text = "agree=" + checked
            }

            ComboBox {
                id: comboColor
                objectName: "qmlColorCombo"
                model: ["Red", "Green", "Blue"]
                onCurrentTextChanged: statusLabel.text = "color=" + currentText
            }
        }

        Slider {
            id: volSlider
            objectName: "qmlVolumeSlider"
            from: 0
            to: 100
            value: 50
            Layout.fillWidth: true
            onValueChanged: statusLabel.text = "vol=" + Math.round(value)
        }

        ListView {
            id: itemList
            objectName: "qmlItemList"
            Layout.fillWidth: true
            Layout.preferredHeight: 100
            clip: true
            model: ["Alpha", "Beta", "Gamma", "Delta"]
            delegate: ItemDelegate {
                width: itemList.width
                text: modelData
                objectName: "qmlItem_" + modelData
                onClicked: statusLabel.text = "selected=" + modelData
            }
        }

        Label {
            id: statusLabel
            objectName: "qmlStatusLabel"
            text: "ready"
            color: "#0066cc"
        }
    }
}
