import SwiftUI
import WebKit
import UIKit

struct WebContainer: UIViewRepresentable {
    @ObservedObject var model: AppModel

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeUIView(context: Context) -> WKWebView {
        let view = model.webView
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        context.coordinator.observe(view)
        if view.url == nil { model.loadHome() }
        return view
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    static func dismantleUIView(_ uiView: WKWebView, coordinator: Coordinator) {
        coordinator.stopObserving(uiView)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        private let model: AppModel
        private var observations: [NSKeyValueObservation] = []
        private var downloadDestinations: [ObjectIdentifier: URL] = [:]

        init(model: AppModel) { self.model = model }

        func observe(_ view: WKWebView) {
            observations = [
                view.observe(\.title, options: [.new]) { [weak self] view, _ in
                    Task { @MainActor in self?.model.pageTitle = view.title?.isEmpty == false ? view.title! : "Prasinos Power" }
                },
                view.observe(\.isLoading, options: [.new]) { [weak self] view, _ in
                    Task { @MainActor in self?.model.isLoading = view.isLoading }
                },
                view.observe(\.canGoBack, options: [.new]) { [weak self] view, _ in
                    Task { @MainActor in self?.model.canGoBack = view.canGoBack }
                }
            ]
        }

        func stopObserving(_ view: WKWebView) { observations.removeAll() }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
            if ["tel", "mailto", "maps"].contains(url.scheme?.lowercased()) {
                UIApplication.shared.open(url); decisionHandler(.cancel); return
            }
            if navigationAction.targetFrame == nil { webView.load(navigationAction.request); decisionHandler(.cancel); return }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            if !navigationResponse.canShowMIMEType || navigationResponse.response.suggestedFilename != nil && navigationResponse.response.mimeType == "application/zip" {
                decisionHandler(.download)
            } else { decisionHandler(.allow) }
        }

        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction,
                     didBecome download: WKDownload) { download.delegate = self }

        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse,
                     didBecome download: WKDownload) { download.delegate = self }

        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                      suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let safeName = suggestedFilename.replacingOccurrences(of: "/", with: "-")
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(safeName)
            try? FileManager.default.removeItem(at: url)
            downloadDestinations[ObjectIdentifier(download)] = url
            completionHandler(url)
        }

        func downloadDidFinish(_ download: WKDownload) {
            guard let url = downloadDestinations.removeValue(forKey: ObjectIdentifier(download)) else { return }
            Task { @MainActor in
                guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
                      let controller = scene.keyWindow?.rootViewController else { return }
                let share = UIActivityViewController(activityItems: [url], applicationActivities: nil)
                controller.present(share, animated: true)
            }
        }

        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            downloadDestinations.removeValue(forKey: ObjectIdentifier(download))
            Task { @MainActor in model.errorMessage = "下载失败：\(error.localizedDescription)" }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            Task { @MainActor in model.errorMessage = "连接服务器失败：\(error.localizedDescription)" }
        }

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let request = navigationAction.request.url { webView.load(URLRequest(url: request)) }
            return nil
        }
    }
}
