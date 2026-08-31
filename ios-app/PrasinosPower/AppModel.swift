import Foundation
import WebKit

@MainActor
final class AppModel: ObservableObject {
    @Published var serverURL: String = UserDefaults.standard.string(forKey: "serverURL") ?? ""
    @Published var pageTitle = "Prasinos Power"
    @Published var isLoading = false
    @Published var canGoBack = false
    @Published var errorMessage: String?
    let webView: WKWebView

    init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.allowsInlineMediaPlayback = true
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        webView.customUserAgent = "PrasinosPower-iOS/1.0"
    }

    private func normalizedURL(from rawValue: String) -> URL? {
        var value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        if !value.contains("://") { value = "https://" + value }
        guard let url = URL(string: value), ["http", "https"].contains(url.scheme?.lowercased()) else { return nil }
        return url
    }

    var normalizedURL: URL? { normalizedURL(from: serverURL) }

    func saveServer(_ value: String) -> Bool {
        guard let url = normalizedURL(from: value) else {
            errorMessage = "请输入有效的服务器地址。"
            return false
        }
        serverURL = url.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        UserDefaults.standard.set(serverURL, forKey: "serverURL")
        errorMessage = nil
        loadHome()
        return true
    }

    func loadHome() {
        guard let baseURL = normalizedURL else { return }
        let url = baseURL.appendingPathComponent("mobile-clock-in")
        webView.load(URLRequest(url: url, cachePolicy: .useProtocolCachePolicy, timeoutInterval: 30))
    }

    func resetServer() {
        webView.stopLoading()
        serverURL = ""
        UserDefaults.standard.removeObject(forKey: "serverURL")
    }
}
