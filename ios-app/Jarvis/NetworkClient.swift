import Foundation

enum NetworkError: LocalizedError {
    case notConfigured
    case badURL
    case server(String)
    case transport(Error)
    case decoding

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "Set your PC's server address and token in Settings first."
        case .badURL:
            return "That server address doesn't look like a valid URL."
        case .server(let message):
            return message
        case .transport(let error):
            return "Couldn't reach Jarvis: \(error.localizedDescription)"
        case .decoding:
            return "Got a response Jarvis's server didn't recognize."
        }
    }
}

/// Talks to the /chat endpoint exposed by jarvis/server.py running on your
/// Windows PC. This is the same "brain" (Claude + PC tools) your Windows
/// tray app uses -- the PC does the actual work, this app just sends text
/// and speaks back whatever it replies with.
struct NetworkClient {
    struct ChatRequestBody: Encodable { let text: String }
    struct ChatResponseBody: Decodable { let reply: String }

    func sendChat(text: String, settings: Settings) async throws -> String {
        guard settings.isConfigured else { throw NetworkError.notConfigured }

        let trimmedBase = settings.serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let base = URL(string: trimmedBase) else { throw NetworkError.badURL }
        let url = base.appendingPathComponent("chat")

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(settings.apiToken)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(ChatRequestBody(text: text))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw NetworkError.transport(error)
        }

        guard let http = response as? HTTPURLResponse else { throw NetworkError.decoding }

        if http.statusCode == 401 {
            throw NetworkError.server("Server rejected the request -- check that the token in Settings matches JARVIS_API_TOKEN in the PC's .env file.")
        }
        guard (200...299).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw NetworkError.server("Server error (\(http.statusCode)): \(body)")
        }

        guard let decoded = try? JSONDecoder().decode(ChatResponseBody.self, from: data) else {
            throw NetworkError.decoding
        }
        return decoded.reply
    }
}
