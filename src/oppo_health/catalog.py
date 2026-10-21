"""Human-readable metadata for every existing OPPO health dataset."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    category: str
    kind: str = "event"
    unit: str | None = None
    time_semantics: str = "优先使用记录时间；若无则使用修改时间"
    caveat: str = "仅呈现云端原始含义；不构成医疗建议"
    available: bool = True

    def public(self) -> dict:
        return asdict(self)


GROUPS: dict[str, str] = {
    "activity": "steps steps_detail steps_stat sport_stat sport_record sport_file sport_goal exercise_intensity tumble sedentary app_usage app_usage_detail",
    "sleep": "sleep sleep_stat sleep_status sleep_index sleep_rr sleep_advice sleep_hr_stat snore_osa_detail snore_osa_sum snore_env_noise snore_feature snore_stat sensor_osa breath_rate",
    "cardiovascular": "heart_rate heart_rate_detail heart_rate_stat heart_rate_warning blood_oxygen blood_oxygen_detail blood_oxygen_stat blood_oxygen_warning blood_oxygen_origin blood_pressure blood_pressure_stat atrial_fibril atrial_fibril_warning cardiovascular ecg hrv",
    "body": "weight weight_target wrist_temperature wrist_temperature_detail blood_sugar blood_sugar_stat blood_sugar_warning vitamin",
    "wellbeing": "stress_detail stress_stat physical_mental_detail physical_mental_stat physical_mental_achievement hearing hearing_stat cervical_stat cervical_action insight",
    "cycle_environment": "physiological_cycle physiological_symptoms ovulation sunshine sunshine_stat",
}

NAMES = {
    "steps": "步数记录", "steps_detail": "步数明细", "steps_stat": "步数日统计", "sport_stat": "运动统计",
    "sport_record": "运动记录版本索引", "sport_file": "加密运动文件", "sport_goal": "运动目标", "exercise_intensity": "运动强度",
    "tumble": "跌倒记录", "sedentary": "久坐提醒", "app_usage": "应用使用统计", "app_usage_detail": "应用使用明细",
    "sleep": "睡眠日记录", "sleep_stat": "睡眠统计", "sleep_status": "睡眠阶段", "sleep_index": "睡眠指数",
    "sleep_rr": "睡眠 RR 间隔", "sleep_advice": "睡眠建议", "sleep_hr_stat": "睡眠心率统计", "snore_osa_detail": "OSA 模型明细",
    "snore_osa_sum": "OSA 汇总", "snore_env_noise": "睡眠环境噪声", "snore_feature": "鼾声特征", "snore_stat": "鼾声统计",
    "sensor_osa": "OSA 传感器数据", "breath_rate": "呼吸率", "heart_rate": "心率记录", "heart_rate_detail": "心率明细",
    "heart_rate_stat": "心率统计", "heart_rate_warning": "心率预警", "blood_oxygen": "血氧记录", "blood_oxygen_detail": "血氧明细",
    "blood_oxygen_stat": "血氧统计", "blood_oxygen_warning": "血氧预警", "blood_oxygen_origin": "血氧设备原始数据",
    "blood_pressure": "血压记录", "blood_pressure_stat": "血压统计", "atrial_fibril": "房颤筛查记录",
    "atrial_fibril_warning": "房颤预警", "cardiovascular": "心血管评估", "ecg": "心电图记录", "hrv": "心率变异性",
    "weight": "体重", "weight_target": "体重目标", "wrist_temperature": "腕温统计", "wrist_temperature_detail": "腕温明细",
    "blood_sugar": "血糖记录", "blood_sugar_stat": "血糖统计", "blood_sugar_warning": "血糖预警", "vitamin": "维生素记录",
    "stress_detail": "压力明细", "stress_stat": "压力统计", "physical_mental_detail": "身心健康明细",
    "physical_mental_stat": "身心健康统计", "physical_mental_achievement": "身心健康成就", "hearing": "听力记录",
    "hearing_stat": "听力统计", "cervical_stat": "颈椎训练统计", "cervical_action": "颈椎训练动作", "insight": "健康洞察",
    "physiological_cycle": "生理周期", "physiological_symptoms": "生理症状", "ovulation": "排卵信息",
    "sunshine": "日照记录", "sunshine_stat": "日照统计", "stress": "压力组合数据", "physical_mental": "身心健康组合数据",
}

DAILY = {"steps_stat", "sport_stat", "sleep", "sleep_stat", "sleep_index", "sleep_hr_stat", "blood_oxygen_stat", "blood_pressure_stat", "blood_sugar_stat", "heart_rate_stat", "stress_stat", "physical_mental_stat", "hearing_stat", "cervical_stat", "snore_stat", "sunshine_stat", "exercise_intensity"}
OBSERVATIONS = {"heart_rate", "heart_rate_detail", "blood_oxygen", "blood_oxygen_detail", "breath_rate", "hrv", "weight", "blood_pressure", "blood_sugar", "wrist_temperature_detail"}
UNITS = {"heart_rate": "bpm", "heart_rate_detail": "bpm", "heart_rate_stat": "bpm", "sleep_hr_stat": "bpm", "blood_oxygen": "%", "blood_oxygen_detail": "%", "steps": "步", "steps_detail": "步", "steps_stat": "步", "weight": "kg", "breath_rate": "次/分"}
COMPOSITES = {"stress": ("stress_detail", "stress_stat"), "physical_mental": ("physical_mental_detail", "physical_mental_stat", "physical_mental_achievement")}


def build_catalog(endpoint_names: list[str] | tuple[str, ...]) -> dict[str, Metric]:
    category_by_key = {key: group for group, keys in GROUPS.items() for key in keys.split()}
    result: dict[str, Metric] = {}
    for key in endpoint_names:
        category = category_by_key.get(key, "other")
        kind = "daily_summary" if key in DAILY else "observation" if key in OBSERVATIONS else "event"
        caveat = ""
        available = True
        if key == "wrist_temperature" or key == "wrist_temperature_detail":
            caveat = "表示相对个人基线变化，不是体温计测得的绝对体温"
        elif key == "sport_file":
            caveat, available = "需要 OPPO 应用运行时生成的 encryptKeyEnc；当前未接入", False
        elif key == "sport_record":
            caveat = "当前数据为版本索引，不是已解密的运动轨迹"
        elif key == "ecg":
            caveat = "当前仅同步记录元数据；波形文件未接入"
        elif key in {"atrial_fibril", "atrial_fibril_warning", "snore_osa_detail", "snore_osa_sum", "physiological_cycle", "physiological_symptoms", "ovulation"}:
            caveat = "源端健康记录，仅作个人数据查看，不作为医学诊断"
        result[key] = Metric(key, NAMES.get(key, key.replace("_", " ").title()), category, kind, UNITS.get(key), caveat=caveat or "单位按字段级源定义确认；未知单位保持未指定", available=available)
    for key, sources in COMPOSITES.items():
        result[key] = Metric(key, NAMES[key], "wellbeing", "composite", None, "由子数据集汇总；不单独请求云端", "数据分组视图，不产生新的测量值")
    return result


def category_names() -> dict[str, str]:
    return {"activity": "活动与运动", "sleep": "睡眠与恢复", "cardiovascular": "心血管", "body": "身体测量", "wellbeing": "压力与身心", "cycle_environment": "女性健康与环境", "other": "其他"}
