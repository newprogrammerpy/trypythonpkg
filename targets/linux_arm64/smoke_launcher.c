#include <Python.h>
#include <stdio.h>

int main(void) {
    Py_Initialize();
    if (!Py_IsInitialized()) {
        return 1;
    }

    puts(Py_GetVersion());
    return Py_FinalizeEx() < 0;
}
