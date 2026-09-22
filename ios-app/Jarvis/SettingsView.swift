import SwiftUI

struct SettingsView: View {
    @ObservedObject var settings = Settings.shared
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            Form {
                Section(
                    header: Text("PC connection"),
                    footer: Text("Use your Windows PC's Tailscale address, e.g. http://jarvis-pc.tailnet-name.ts.net:8731 -- see README for setup. Must include http:// and the port.")
                ) {
                    TextField("Server URL", text: $settings.serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("API token", text: $settings.apiToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section(footer: Text("The token must match JARVIS_API_TOKEN in the .env file on your PC.")) {
                    EmptyView()
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
