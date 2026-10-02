import Foundation
import Security

// usage-helper: prints Claude subscription usage (5-hour and weekly) as JSON for
// the MiniToo dashboard. It reads Claude Code's login from the Keychain and makes
// one request to the endpoint Claude Code's /usage uses. That endpoint is
// undocumented and may change without notice.
//
// Only percentages and reset times leave this process; the token is never
// printed, logged or refreshed (refreshing could sign Claude Code out).
// Being a separate binary, it is the only program the user's "Always Allow"
// Keychain grant applies to.
//
// Without --interactive it never shows the Keychain prompt: when access is
// missing (for example after Claude Code rewrote the item) it reports
// "needs_access" at once. The daemon runs it that way; prompts piled up
// unanswered otherwise. `minitoo-dashboard grant-keychain` passes --interactive.

let service = "Claude Code-credentials"
let interactive = CommandLine.arguments.dropFirst().contains("--interactive")
let endpoint = URL(string: "https://api.anthropic.com/api/oauth/usage")!

func emit(_ object: [String: Any]) -> Never {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{\"status\":\"error\"}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(object["status"] as? String == "ok" ? 0 : 1)
}

func readCredentials() -> Data {
    var query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service,
        kSecReturnData as String: true,
        kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    if !interactive {
        // The login keychain is a file keychain: its ACL prompt obeys the legacy switch.
        SecKeychainSetUserInteractionAllowed(false)
        query[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
    }
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if !interactive { SecKeychainSetUserInteractionAllowed(true) }  // restore the default at once
    switch status {
    case errSecSuccess:
        guard let data = item as? Data else { emit(["status": "format", "detail": "keychain item has no data"]) }
        return data
    case errSecItemNotFound:
        emit(["status": "no_token", "detail": "Claude Code login not found in Keychain"])
    case _ where !interactive && (status == errSecInteractionNotAllowed || status == errSecAuthFailed):
        // With the prompt suppressed, a missing grant comes back as either code.
        emit(["status": "needs_access", "detail": "no Keychain access for usage-helper (prompt suppressed)"])
    case errSecUserCanceled, errSecAuthFailed, errSecInteractionNotAllowed:
        emit(["status": "keychain_denied", "detail": "Keychain access was not allowed (OSStatus \(status))"])
    default:
        emit(["status": "error", "detail": "Keychain OSStatus \(status)"])
    }
}

let credentials = readCredentials()
guard let root = try? JSONSerialization.jsonObject(with: credentials) as? [String: Any],
      let oauth = root["claudeAiOauth"] as? [String: Any],
      let token = oauth["accessToken"] as? String, !token.isEmpty else {
    emit(["status": "format", "detail": "unexpected Keychain item format"])
}
if let expiresMs = oauth["expiresAt"] as? Double, expiresMs / 1000 < Date().timeIntervalSince1970 {
    emit(["status": "expired", "detail": "token expired; it renews next time Claude Code runs"])
}

var request = URLRequest(url: endpoint, timeoutInterval: 15)
request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
request.setValue("oauth-2025-04-20", forHTTPHeaderField: "anthropic-beta")
request.setValue("minitoo-dashboard", forHTTPHeaderField: "User-Agent")

let sema = DispatchSemaphore(value: 0)
var output: [String: Any] = ["status": "network", "detail": "no response"]
URLSession.shared.dataTask(with: request) { data, response, error in
    defer { sema.signal() }
    if let error = error {
        output = ["status": "network", "detail": error.localizedDescription]
        return
    }
    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard code == 200 else {
        output = ["status": "http_\(code)", "detail": code == 401 ? "token rejected" : "unexpected HTTP status"]
        return
    }
    guard let data = data, let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        output = ["status": "format", "detail": "response is not JSON"]
        return
    }
    var result: [String: Any] = ["status": "ok"]
    for key in ["five_hour", "seven_day"] {
        if let window = body[key] as? [String: Any] {
            result[key] = ["utilization": window["utilization"] ?? NSNull(),
                           "resets_at": window["resets_at"] ?? NSNull()]
        }
    }
    output = result
}.resume()
_ = sema.wait(timeout: .now() + 20)
emit(output)
