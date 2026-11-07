#import <UIKit/UIKit.h>
#include <Python/Python.h>

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

static BOOL StartEmbeddedPython(void) {
    NSBundle *bundle = NSBundle.mainBundle;
    NSString *resources = bundle.resourcePath;
    NSString *pythonHome = [resources stringByAppendingPathComponent:@"python"];
    NSString *libraryRoot = [pythonHome stringByAppendingPathComponent:@"lib"];
    NSError *error = nil;
    NSArray<NSString *> *libraryEntries =
        [[NSFileManager defaultManager] contentsOfDirectoryAtPath:libraryRoot
                                                            error:&error];
    if (libraryEntries == nil) {
        NSLog(@"Python runtime library directory is unavailable: %@", error);
        return NO;
    }

    NSString *versionedLibrary = nil;
    for (NSString *entry in [libraryEntries sortedArrayUsingSelector:@selector(compare:)]) {
        if ([entry hasPrefix:@"python3."]) {
            versionedLibrary = [libraryRoot stringByAppendingPathComponent:entry];
            break;
        }
    }
    if (versionedLibrary == nil) {
        NSLog(@"No versioned Python standard library was bundled.");
        return NO;
    }

    NSString *dynamicLibraryPath =
        [versionedLibrary stringByAppendingPathComponent:@"lib-dynload"];
    NSString *appPath = [resources stringByAppendingPathComponent:@"app"];
    NSString *pythonPath =
        [NSString stringWithFormat:@"%@:%@:%@", versionedLibrary, dynamicLibraryPath, appPath];
    if (setenv("PYTHONHOME", pythonHome.UTF8String, 1) != 0 ||
        setenv("PYTHONPATH", pythonPath.UTF8String, 1) != 0) {
        NSLog(@"Could not configure the embedded Python environment.");
        return NO;
    }

    PyPreConfig preconfig;
    PyPreConfig_InitPythonConfig(&preconfig);
    preconfig.utf8_mode = 1;
    PyStatus status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) {
        NSLog(@"Python pre-initialization failed: %s", status.err_msg ?: "unknown error");
        return NO;
    }

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.buffered_stdio = 0;
    config.write_bytecode = 0;
    config.install_signal_handlers = 1;
    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        NSLog(@"Python initialization failed: %s", status.err_msg ?: "unknown error");
        return NO;
    }

    int result = PyRun_SimpleString(
        "import runpy\n"
        "runpy.run_module('main', run_name='__main__')\n");
    if (result != 0) {
        PyErr_Print();
        return NO;
    }
    return YES;
}

@implementation AppDelegate

- (BOOL)application:(UIApplication *)application
        didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.rootViewController = [[UIViewController alloc] init];
    [self.window makeKeyAndVisible];
    return StartEmbeddedPython();
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass(AppDelegate.class));
    }
}
