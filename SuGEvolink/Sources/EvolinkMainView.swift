import SwiftUI

struct EvolinkMainView: View {
    @StateObject private var health = EvolinkHealthService()
    @StateObject private var bridge = EvolinkWebBridge()
    @AppStorage("evolinkAllowNativeHealthDisplay") private var allowHealthDisplay = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("S.u.G EVOLINK")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(Color(red: 0.92, green: 0.76, blue: 0.42))
                Spacer(minLength: 6)
                if health.isLoading { ProgressView().tint(.white) }
                HStack(spacing: 4) {
                    Text("画面へ反映")
                        .font(.caption2)
                    Toggle("画面へ反映", isOn: $allowHealthDisplay)
                        .labelsHidden()
                        .accessibilityLabel("本人同意で取得データをEVOLINK内画面へ反映")
                }
                    .onChange(of: allowHealthDisplay) { _, enabled in
                        if enabled, let payload = health.latestPayload { bridge.deliver(payload) }
                        if !enabled { bridge.clear() }
                    }
                Button {
                    Task {
                        await health.refreshFromDevice()
                        if allowHealthDisplay, let payload = health.latestPayload { bridge.deliver(payload) }
                        else { bridge.clear() }
                    }
                } label: {
                    Label("HEALTH同期", systemImage: "heart.text.square")
                        .font(.caption.weight(.semibold))
                }
                .disabled(health.isLoading)
                .accessibilityIdentifier("evolinkHealthSync")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Color(red: 0.05, green: 0.13, blue: 0.12))

            EvolinkWebView(bridge: bridge, onRefresh: {
                Task {
                    await health.refreshFromDevice()
                    if allowHealthDisplay, let payload = health.latestPayload { bridge.deliver(payload) }
                    else { bridge.clear() }
                }
            })
            .background(.black)

            Text(health.status)
                .font(.caption2)
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(Color(red: 0.05, green: 0.13, blue: 0.12))
                .accessibilityIdentifier("evolinkSyncStatus")
        }
    }
}
