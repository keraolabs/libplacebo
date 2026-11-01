#import <UIKit/UIKit.h>

#include "metal_tests.h"

// The stack size of the main thread, which the tests were written for
#define TEST_THREAD_STACK_SIZE (8 << 20)

@interface PLTestSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

@implementation PLTestSceneDelegate

- (void)scene:(UIScene *)scene
    willConnectToSession:(UISceneSession *)session
                 options:(UISceneConnectionOptions *)options
{
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *) scene];
    self.window.rootViewController = [[UIViewController alloc] init];
    [self.window makeKeyAndVisible];
}

@end

@interface PLTestAppDelegate : UIResponder <UIApplicationDelegate>
@end

@implementation PLTestAppDelegate

- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary *)options
{
    application.idleTimerDisabled = YES;
    NSThread *tests = [[NSThread alloc] initWithBlock:^{
        int status = metal_tests();
        dispatch_async(dispatch_get_main_queue(), ^{
            exit(status);
        });
    }];
    tests.stackSize = TEST_THREAD_STACK_SIZE;
    [tests start];
    return YES;
}

- (UISceneConfiguration *)application:(UIApplication *)application
    configurationForConnectingSceneSession:(UISceneSession *)session
                                   options:(UISceneConnectionOptions *)options
{
    UISceneConfiguration *config =
        [[UISceneConfiguration alloc] initWithName:nil sessionRole:session.role];
    config.delegateClass = [PLTestSceneDelegate class];
    return config;
}

@end

int main(int argc, char *argv[])
{
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil,
                                 NSStringFromClass([PLTestAppDelegate class]));
    }
}
