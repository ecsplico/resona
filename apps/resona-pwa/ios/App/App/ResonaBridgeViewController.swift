import Capacitor
import UIKit

/// The app's Capacitor view controller: registers the app-local plugins, which live in this
/// target instead of an npm package (Capacitor 8 only auto-registers packaged plugins from
/// `packageClassList`). Used by `SceneDelegate` and `Main.storyboard`.
class ResonaBridgeViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(ResonaSpeechPlugin())
    }
}
