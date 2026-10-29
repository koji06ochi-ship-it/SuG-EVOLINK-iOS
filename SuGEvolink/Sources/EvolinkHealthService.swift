import Foundation
import HealthKit
import Combine

@MainActor
final class EvolinkHealthService: ObservableObject {
    @Published private(set) var isLoading = false
    @Published private(set) var status = "ヘルスケア未同期。本人の許可後に取得します。"
    @Published private(set) var latestPayload: [String: Any]?

    private let store = HKHealthStore()
    private let quantityTypes: [HKQuantityTypeIdentifier] = [
        .stepCount, .distanceWalkingRunning, .activeEnergyBurned,
        .heartRate, .restingHeartRate, .heartRateVariabilitySDNN
    ]

    func refreshFromDevice() async {
        guard !isLoading else { return }
        latestPayload = nil
        guard HKHealthStore.isHealthDataAvailable() else {
            status = "この端末はHealthKitを利用できません。"; return
        }
        isLoading = true
        defer { isLoading = false }
        status = "HealthKitの許可とデータ取得を確認中…"
        var readTypes: Set<HKObjectType> = []
        for id in quantityTypes {
            if let t = HKObjectType.quantityType(forIdentifier: id) { readTypes.insert(t) }
        }
        if let sleepType = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) { readTypes.insert(sleepType) }
        do {
            try await store.requestAuthorization(toShare: [], read: readTypes)
        } catch {
            status = "ヘルスケアへのアクセスを確認できませんでした。"
            return
        }
        let end = Date()
        let result = await EvolinkHealthReader(store:store).read(end:end)
        guard !result.metrics.isEmpty else {
            status = "取得可能なデータがありません。読取拒否と未記録は区別できません。"; return
        }
        latestPayload = ["source":"healthkit_native_evolink","verifiedMeasurement":true,
            "syncedAt":ISO8601DateFormatter().string(from:end),"metrics":result.metrics,"metadata":result.metadata]
        status = "HealthKit取得値を確認できます。測定時刻・期間・取得元を各画面に表示します。"
    }
}
