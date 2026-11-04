import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, configurationForConnecting connectingSceneSession: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)
        config.delegateClass = SceneDelegate.self; return config
    }
}

final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private var controller: RemoteViewController?
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let controller = RemoteViewController(); self.controller = controller
        let window = UIWindow(windowScene: scene); self.window = window
        window.rootViewController = controller; window.makeKeyAndVisible()
    }
    func sceneDidBecomeActive(_ scene: UIScene) { controller?.resume() }
    func sceneWillResignActive(_ scene: UIScene) { controller?.pause() }
    func sceneDidEnterBackground(_ scene: UIScene) { controller?.pause() }
    func sceneDidDisconnect(_ scene: UIScene) { controller?.pause() }
}
