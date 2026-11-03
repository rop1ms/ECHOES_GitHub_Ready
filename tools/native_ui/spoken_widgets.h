#pragma once
#include <QtWidgets/QPushButton>

// Give native-painted buttons a distinct Qt meta-class so the host's ordinary
// QPushButton raster cache does not replace their custom painting.
class NativeSpokenButton : public QPushButton {
    Q_OBJECT
public:
    using QPushButton::QPushButton;
};
