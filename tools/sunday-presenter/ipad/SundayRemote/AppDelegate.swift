import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        if #available(iOS 26.0, *) {
            UIMainMenuSystem.shared.setBuildConfiguration(UIMainMenuSystem.Configuration())
        }
        return true
    }
    override func buildMenu(with builder: UIMenuBuilder) {
        super.buildMenu(with: builder)
        guard builder.system == UIMenuSystem.main else { return }
        let about = UIMenu(title: "", identifier: .about, options: .displayInline, children: [
            UICommand(title: "關於 Sunday Remote", action: #selector(RemoteViewController.showAbout))
        ])
        let close = UIMenu(title: "", identifier: .quit, options: .displayInline, children: [
            UIKeyCommand(title: "關閉 Sunday Remote", action: #selector(RemoteViewController.closeApp), input: "q", modifierFlags: .command)
        ])
        if builder.menu(for: .application) == nil {
            builder.insertChild(UIMenu(title: "Sunday Remote", identifier: .application, children: [about, close]), atStartOfMenu: .root)
        } else {
            if builder.menu(for: .about) != nil { builder.replace(menu: .about, with: about) }
            else { builder.insertChild(about, atStartOfMenu: .application) }
            if builder.menu(for: .quit) != nil { builder.replace(menu: .quit, with: close) }
            else { builder.insertChild(close, atEndOfMenu: .application) }
        }
    }
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
