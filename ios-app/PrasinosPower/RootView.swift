import SwiftUI

struct RootView: View {
    @StateObject private var model = AppModel()
    @State private var showSettings = false

    var body: some View {
        Group {
            if model.serverURL.isEmpty {
                ServerSetupView(model: model)
            } else {
                webApp
            }
        }
        .tint(Color(red: 15/255, green: 118/255, blue: 110/255))
        .sheet(isPresented: $showSettings) { ServerSetupView(model: model, isSheet: true) }
        .alert("无法打开", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("好", role: .cancel) {}
        } message: { Text(model.errorMessage ?? "未知错误") }
    }

    private var webApp: some View {
        NavigationStack {
            WebContainer(model: model)
                .ignoresSafeArea(.container, edges: .bottom)
                .navigationTitle(model.pageTitle)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItemGroup(placement: .bottomBar) {
                        Button { model.webView.goBack() } label: { Image(systemName: "chevron.backward") }
                            .disabled(!model.canGoBack)
                        Button { model.loadHome() } label: { Image(systemName: "house") }
                        Spacer()
                        if model.isLoading { ProgressView().controlSize(.small) }
                        Spacer()
                        Button { model.webView.reload() } label: { Image(systemName: "arrow.clockwise") }
                        Button { showSettings = true } label: { Image(systemName: "gearshape") }
                    }
                }
        }
    }
}

private struct ServerSetupView: View {
    @ObservedObject var model: AppModel
    var isSheet = false
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("团队服务器") {
                    TextField("https://office.example.com", text: $draft)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                    Text("推荐使用 HTTPS 域名。仅在同一局域网内使用时，也可填写 http://NAS地址:8088。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section {
                    Button("保存并连接") {
                        if model.saveServer(draft), isSheet { dismiss() }
                    }
                    .frame(maxWidth: .infinity)
                    if isSheet {
                        Button("清除服务器设置", role: .destructive) {
                            model.resetServer(); dismiss()
                        }.frame(maxWidth: .infinity)
                    }
                }
            }
            .navigationTitle(isSheet ? "连接设置" : "Prasinos Power")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear { draft = model.serverURL }
            .toolbar { if isSheet { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } } } }
        }
    }
}
