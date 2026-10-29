import SwiftUI
import WebKit
import Combine

@MainActor
final class EvolinkWebBridge: ObservableObject {
    weak var webView: WKWebView?
    private var pendingPayload: [String: Any]?

    func deliver(_ payload: [String: Any]) {
        pendingPayload = payload
        flush()
    }

    func clear() {
        pendingPayload = nil
        webView?.evaluateJavaScript("window.SuGEvolinkNativeHealth?.clear?.();", completionHandler: nil)
    }

    func flush() {
        guard let webView, let payload = pendingPayload,
              JSONSerialization.isValidJSONObject(payload),
              let raw = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: raw, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.SuGEvolinkNativeHealth && window.SuGEvolinkNativeHealth.receive(\(json));") { [weak self] accepted, error in
            if error == nil, accepted as? Bool == true { self?.pendingPayload = nil }
        }
    }
}

struct EvolinkWebView: UIViewRepresentable {
    @ObservedObject var bridge: EvolinkWebBridge
    let onRefresh: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(bridge: bridge, onRefresh: onRefresh) }

    func makeUIView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        controller.add(context.coordinator, name: "sugEvolinkHealth")
        controller.add(context.coordinator, name: "sugEvolinkFiles")
        for resource in ["native-health-bridge", "native-files-bridge"] {
        if let jsURL = Bundle.main.url(forResource: resource, withExtension: "js", subdirectory: "WebContent"),
           let js = try? String(contentsOf: jsURL, encoding: .utf8) {
            controller.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController = controller
        let preferences = WKWebpagePreferences()
        preferences.allowsContentJavaScript = true
        config.defaultWebpagePreferences = preferences
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0.03, green: 0.08, blue: 0.08, alpha: 1)
        webView.allowsBackForwardNavigationGestures = false
        webView.isInspectable = false
        bridge.webView = webView
        if let html = Bundle.main.url(forResource: "EVOLINK_OFFLINE", withExtension: "html", subdirectory: "WebContent") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        } else {
            webView.loadHTMLString("<html lang='ja'><body style='background:#092725;color:white;font:16px sans-serif;padding:32px'><h1>EVOLINKの正本が未配置</h1><p>この候補版は配信できません。最新EVOLINK_OFFLINE.htmlを組み込んでください。</p></body></html>", baseURL: nil)
        }
        return webView
    }
    func updateUIView(_ uiView: WKWebView, context: Context) { bridge.webView = uiView }

    @MainActor final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        let bridge: EvolinkWebBridge
        let onRefresh: () -> Void
        private let fileExport = EvolinkFileExport()
        init(bridge: EvolinkWebBridge, onRefresh: @escaping () -> Void) { self.bridge = bridge; self.onRefresh = onRefresh }
        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.frameInfo.isMainFrame, let view = message.webView, view.url?.isFileURL == true,
                  let body = message.body as? [String: String] else { return }
            if message.name == "sugEvolinkHealth", body["action"] == "refresh" { onRefresh() }
            if message.name == "sugEvolinkFiles", body["action"] == "exportJSON" { fileExport.export(body, from: view) }
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { bridge.flush() }
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url, url.isFileURL,
                  let resources = Bundle.main.resourceURL?.appendingPathComponent("WebContent").standardizedFileURL.path,
                  url.standardizedFileURL.path.hasPrefix(resources + "/") else {
                decisionHandler(.cancel); return
            }
            decisionHandler(.allow)
        }
    }
}
