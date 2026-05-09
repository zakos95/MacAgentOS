// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "NativeMacApp",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "NativeMacApp", targets: ["NativeMacApp"])
    ],
    targets: [
        .executableTarget(
            name: "NativeMacApp",
            path: "Sources"
        )
    ]
)
