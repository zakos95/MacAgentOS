import Foundation
import XCTest
@testable import NativeMacApp

final class MCPImportTests: XCTestCase {
    func testImportKeepsOnlyServerConfigurationAndResolvesWorkingDirectory() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let original = directory.appending(path: ".mcp.json")
        let raw = """
            {"unrelatedPreference":"private-value","mcpServers":{
                "one":{"command":"node","args":["./server.js"]},
                "two":{"type":"stdio","command":"python3","cwd":"tools"},
                "three":{"type":"http","url":"https://example.org/rpc?token=secret","headers":{"Key":"secret"}}}}
            """
        try writePrivateFile(Data(raw.utf8), to: original)
        let imported = try ClaudeMCPImport(file: original)
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: imported.data) as? [String: Any])
        XCTAssertEqual(Array(root.keys), ["mcpServers"])
        let servers = try XCTUnwrap(root["mcpServers"] as? [String: [String: Any]])
        XCTAssertEqual(servers["one"]?["cwd"] as? String, directory.path)
        XCTAssertEqual(servers["two"]?["cwd"] as? String, directory.appending(path: "tools").path)
        XCTAssertEqual((servers["three"]?["headers"] as? [String: String])?["Key"], "secret")
        XCTAssertFalse(imported.previews.joined().contains("secret"))
        XCTAssertTrue(imported.previews.joined().contains("./server.js"))
        let saved = directory.appending(path: "saved.json")
        try writePrivateFile(imported.data, to: saved)
        let attributes = try FileManager.default.attributesOfItem(atPath: saved.path)
        XCTAssertEqual(attributes[.posixPermissions] as? Int, 0o600)
        XCTAssertEqual(try Data(contentsOf: saved), imported.data)
    }

    func testRejectsMalformedConfigWithoutReplacingExistingImport() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appending(path: ".mcp.json")
        for invalid in ["{}", #"{"mcpServers":{"x":{"command":""}}}"#,
                        #"{"mcpServers":{"x":{"type":"sse"}}}"#] {
            try writePrivateFile(Data(invalid.utf8), to: file)
            XCTAssertThrowsError(try ClaudeMCPImport(file: file))
        }
        try writePrivateFile(Data(#"{"mcpServers":{}}"#.utf8), to: file)
        XCTAssertTrue(try ClaudeMCPImport(file: file).previews.isEmpty)
    }
}
