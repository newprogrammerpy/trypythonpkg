#include <jni.h>
#include <Python.h>
#include <unistd.h>

static void throw_runtime_exception(JNIEnv *env, const char *message) {
    jclass exception_class = (*env)->FindClass(env, "java/lang/RuntimeException");
    if (exception_class != NULL) {
        (*env)->ThrowNew(env, exception_class, message != NULL ? message : "Python startup failed");
    }
}

JNIEXPORT jint JNICALL
Java_org_pypack_android_MainActivity_runPython(
    JNIEnv *env, jclass class, jstring home, jstring script
) {
    const char *home_path = (*env)->GetStringUTFChars(env, home, NULL);
    const char *script_path = (*env)->GetStringUTFChars(env, script, NULL);
    if (home_path == NULL || script_path == NULL) {
        if (home_path != NULL) (*env)->ReleaseStringUTFChars(env, home, home_path);
        if (script_path != NULL) (*env)->ReleaseStringUTFChars(env, script, script_path);
        return 1;
    }

    if (chdir(home_path) != 0) {
        throw_runtime_exception(env, "Unable to set the Python application directory.");
        (*env)->ReleaseStringUTFChars(env, home, home_path);
        (*env)->ReleaseStringUTFChars(env, script, script_path);
        return 1;
    }

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    PyStatus status = PyConfig_SetBytesString(&config, &config.home, home_path);
    if (!PyStatus_Exception(status)) {
        status = PyConfig_SetBytesString(&config, &config.run_filename, script_path);
    }
    if (!PyStatus_Exception(status)) {
        status = Py_InitializeFromConfig(&config);
    }
    if (PyStatus_Exception(status)) {
        throw_runtime_exception(env, status.err_msg);
        PyConfig_Clear(&config);
        (*env)->ReleaseStringUTFChars(env, home, home_path);
        (*env)->ReleaseStringUTFChars(env, script, script_path);
        return 1;
    }

    PyConfig_Clear(&config);
    (*env)->ReleaseStringUTFChars(env, home, home_path);
    (*env)->ReleaseStringUTFChars(env, script, script_path);

    return Py_RunMain();
}
