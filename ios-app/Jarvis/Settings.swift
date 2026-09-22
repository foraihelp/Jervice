import Foundation
import Combine

/// Persists the two things this app needs to know to reach your Windows
/// PC: its address (a Tailscale hostname/IP is recommended -- see README)
/// and the shared bearer token from your PC's .env (JARVIS_API_TOKEN).
final class Settings: ObservableObject {
    static let shared = Settings()

    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: "jarvis.serverURL") }
    }
    @Published var apiToken: String {
        didSet { UserDefaults.standard.set(apiToken, forKey: "jarvis.apiToken") }
    }

    private init() {
        // Example: "http://jarvis-pc.tailnet-name.ts.net:8731"
        self.serverURL = UserDefaults.standard.string(forKey: "jarvis.serverURL") ?? ""
        self.apiToken = UserDefaults.standard.string(forKey: "jarvis.apiToken") ?? ""
    }

    var isConfigured: Bool {
        !serverURL.trimmingCharacters(in: .whitespaces).isEmpty &&
        !apiToken.trimmingCharacters(in: .whitespaces).isEmpty
    }
}
