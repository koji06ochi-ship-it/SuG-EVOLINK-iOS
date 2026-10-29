import Foundation
import HealthKit

@MainActor
final class EvolinkHealthReader {
    let store: HKHealthStore
    init(store: HKHealthStore) { self.store = store }
    private struct Reading { let value: Double; let metadata: [String: Any] }
    func read(end: Date) async -> (metrics: [String:Double], metadata: [String:Any]) {
        let today = Calendar.current.startOfDay(for:end)
        var values: [String:Double] = [:], metadata: [String:Any] = [:]
        let cumulative: [(String, HKQuantityTypeIdentifier, HKUnit, Double)] = [
            ("steps",.stepCount,.count(),1), ("distanceKm",.distanceWalkingRunning,.meter(),1000),
            ("activeEnergyKcal",.activeEnergyBurned,.kilocalorie(),1)]
        for (key,id,unit,divisor) in cumulative {
            if let r = await sum(id,unit:unit,start:today,end:end) { values[key] = r.value/divisor; metadata[key] = r.metadata }
        }
        let recent: [(String, HKQuantityTypeIdentifier, HKUnit)] = [
            ("heartRate",.heartRate,HKUnit.count().unitDivided(by:.minute())),
            ("restingHeartRate",.restingHeartRate,HKUnit.count().unitDivided(by:.minute())),
            ("hrvMs",.heartRateVariabilitySDNN,.secondUnit(with:.milli))]
        for (key,id,unit) in recent {
            if let r = await latest(id,unit:unit,end:end) { values[key] = r.value; metadata[key] = r.metadata }
        }
        if let r = await sleep(end:end) { values["sleepHours"] = r.value; metadata["sleepHours"] = r.metadata }
        return (values,metadata)
    }
    nonisolated private static func source(_ value: HKSource) -> [String:String] {
        ["name":value.name,"bundleIdentifier":value.bundleIdentifier]
    }
    nonisolated private static func iso(_ date: Date) -> String { ISO8601DateFormatter().string(from:date) }
    private func sum(_ id: HKQuantityTypeIdentifier, unit: HKUnit, start: Date, end: Date) async -> Reading? {
        guard let type = HKObjectType.quantityType(forIdentifier:id) else { return nil }
        let predicate = HKQuery.predicateForSamples(withStart:start,end:end,options:.strictStartDate)
        return await withCheckedContinuation { continuation in
            let query = HKStatisticsQuery(quantityType:type,quantitySamplePredicate:predicate,options:[.cumulativeSum, .separateBySource]) { _, stats, _ in
                guard let value = stats?.sumQuantity()?.doubleValue(for:unit) else { continuation.resume(returning:nil); return }
                let sources = stats?.sources?.map { Self.source($0) } ?? []
                continuation.resume(returning:Reading(value:value,metadata:["kind":"period","aggregation":"HealthKit cumulativeSum",
                    "periodStart":Self.iso(start),"periodEnd":Self.iso(end),"sources":sources,"timeZone":TimeZone.current.identifier,
                    "label":"本日0時から取得時刻までの集計（単一の測定時刻ではありません）"]))
            }
            store.execute(query)
        }
    }
    private func latest(_ id: HKQuantityTypeIdentifier, unit: HKUnit, end: Date) async -> Reading? {
        guard let type = HKObjectType.quantityType(forIdentifier:id) else { return nil }
        let predicate = HKQuery.predicateForSamples(withStart:end.addingTimeInterval(-86400),end:end,options:.strictEndDate)
        return await withCheckedContinuation { continuation in
            let query = HKSampleQuery(sampleType:type,predicate:predicate,limit:1,
                sortDescriptors:[NSSortDescriptor(key:HKSampleSortIdentifierEndDate,ascending:false)]) { _, items, _ in
                guard let sample = items?.first as? HKQuantitySample else { continuation.resume(returning:nil); return }
                var metadata: [String:Any] = ["kind":"sample","observedStart":Self.iso(sample.startDate),"observedEnd":Self.iso(sample.endDate),
                    "sources":[Self.source(sample.sourceRevision.source)],"timeZone":TimeZone.current.identifier,"label":"直近24時間の最新記録"]
                if let manual = sample.metadata?[HKMetadataKeyWasUserEntered] as? Bool { metadata["userEntered"] = manual }
                continuation.resume(returning:Reading(value:sample.quantity.doubleValue(for:unit),metadata:metadata))
            }
            store.execute(query)
        }
    }
    private func sleep(end: Date) async -> Reading? {
        guard let type = HKObjectType.categoryType(forIdentifier:.sleepAnalysis) else { return nil }
        let start = end.addingTimeInterval(-86400)
        // Overlapping records are included, clipped to the window, then unioned.
        let predicate = HKQuery.predicateForSamples(withStart:start,end:end,options:[])
        return await withCheckedContinuation { continuation in
            let query = HKSampleQuery(sampleType:type,predicate:predicate,limit:HKObjectQueryNoLimit,sortDescriptors:nil) { _, items, _ in
                let samples = (items as? [HKCategorySample] ?? []).filter {
                    [HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue,HKCategoryValueSleepAnalysis.asleepCore.rawValue,
                     HKCategoryValueSleepAnalysis.asleepDeep.rawValue,HKCategoryValueSleepAnalysis.asleepREM.rawValue].contains($0.value)
                }
                let intervals = samples.map { (max(start,$0.startDate),min(end,$0.endDate)) }.filter { $0.1 > $0.0 }.sorted { $0.0 < $1.0 }
                guard !intervals.isEmpty else { continuation.resume(returning:nil); return }
                var total = 0.0
                var current: (Date,Date)?
                for interval in intervals {
                    if let previous = current {
                        if interval.0 <= previous.1 { current = (previous.0,max(previous.1,interval.1)) }
                        else { total += previous.1.timeIntervalSince(previous.0); current = interval }
                    } else { current = interval }
                }
                if let previous = current { total += previous.1.timeIntervalSince(previous.0) }
                let groups = Dictionary(grouping:samples,by: { $0.sourceRevision.source.bundleIdentifier })
                let sources = groups.values.compactMap { $0.first }.map { Self.source($0.sourceRevision.source) }
                continuation.resume(returning:Reading(value:total/3600,metadata:["kind":"period","aggregation":"unionAsleepIntervals",
                    "periodStart":Self.iso(start),"periodEnd":Self.iso(end),"sources":sources,"timeZone":TimeZone.current.identifier,
                    "label":"直近24時間の睡眠区間（境界を切取り・重複を除外）"]))
            }
            store.execute(query)
        }
    }
}
