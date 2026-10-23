"""Human-readable metadata for every existing OPPO health dataset."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any


KIND_LABELS: dict[str, str] = {
    "event": "事件 / 评估",
    "daily_summary": "日统计",
    "observation": "测量 / 采样",
    "interval": "时间段 / 阶段",
    "index": "指数 / 评分",
    "snapshot": "目标 / 成就 / 快照",
    "composite": "组合视图",
}

KIND_EXPLANATIONS: dict[str, str] = {
    "event": "一次源端事件、提醒、预警或评估记录，通常由多个字段组成，不一定有单一数值。",
    "daily_summary": "按天或周期汇总的记录，多个统计字段会保留在明细里，不强行压成一个数字。",
    "observation": "一次测量或采样记录；如果源端同时返回类型、设备和上下文，会一并保留。",
    "interval": "带开始/结束时间的区间、阶段或动作记录；重点查看时间范围和状态字段。",
    "index": "源端给出的指数、评分或等级；只展示原始定义，不自行解释为医学结论。",
    "snapshot": "目标、成就、建议或当前状态的快照；它不是连续测量值。",
    "composite": "由多个子数据集拼成的组合视图，不会单独请求一个云端接口。",
}

DATA_SHAPES: dict[str, str] = {
    "event": "多字段结构化记录",
    "daily_summary": "多字段统计记录",
    "observation": "测量/采样记录",
    "interval": "时间区间记录",
    "index": "评分/指数记录",
    "snapshot": "状态快照记录",
    "composite": "子数据集组合",
}


PHYSICAL_MENTAL_FIELD_INTERPRETATION = (
    "该字段在身心健康指标中表示身心健康值，不是心理压力强度；"
    "数值越高表示身心状态越好，数值越低表示需要适当放松。"
)

PHYSICAL_MENTAL_VALUE_CONTEXT: dict[str, Any] = {
    "display_name": "身心健康值",
    "value_direction": "higher_is_better",
    "scale": {"min": 1, "max": 100},
    "official_definition": (
        "身心状态基于心率变异性(HRV)、静息心率和活动强度来综合评估身体疲劳程度和心理情绪状态，"
        "能够帮助你及时了解自身状态，应对工作生活中的挑战。"
    ),
    "interpretation": PHYSICAL_MENTAL_FIELD_INTERPRETATION,
    "levels": [
        {"min": 76, "max": 100, "label": "状态优秀"},
        {"min": 51, "max": 75, "label": "状态不错"},
        {"min": 26, "max": 50, "label": "状态正常"},
        {"min": 1, "max": 25, "label": "适当放松"},
    ],
    "notes": [
        "身心状态基于生理指标变化推测，与用户感知的心理情绪状态不一定完全相符。",
        "身心状态的短期波动是正常现象，建议关注长期趋势；如果对波动有疑问，可以参考健康APP内的全天HRV数值图表。",
    ],
    "field_semantics": {
        field: {
            "display_name": "身心健康值",
            "value_direction": "higher_is_better",
            "interpretation": PHYSICAL_MENTAL_FIELD_INTERPRETATION,
        }
        for field in ("stressValue", "stress", "score")
    },
}

PHYSICAL_MENTAL_METRICS = {
    "physical_mental_detail",
    "physical_mental_stat",
    "physical_mental_achievement",
    "physical_mental",
}


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
    description: str = ""
    field_hints: tuple[str, ...] = ()
    value_context: dict[str, Any] | None = None

    def value_context_for_field(self, field: str) -> dict[str, Any] | None:
        """Return field-scoped semantics without exposing unrelated metric fields."""
        if not self.value_context:
            return None
        semantics = self.value_context.get("field_semantics", {}).get(field)
        if not semantics:
            return None
        context = {
            key: deepcopy(self.value_context[key])
            for key in ("display_name", "value_direction", "scale", "levels", "interpretation")
            if key in self.value_context
        }
        context.update(deepcopy(semantics))
        context["field"] = field
        return context

    def public(self) -> dict:
        data = asdict(self)
        data["kind_label"] = KIND_LABELS.get(self.kind, self.kind)
        data["kind_explanation"] = KIND_EXPLANATIONS.get(self.kind, "按源端字段保留")
        data["data_shape"] = DATA_SHAPES.get(self.kind, "结构化记录")
        data["field_hints"] = list(self.field_hints)
        if self.value_context is None:
            data.pop("value_context", None)
        return data


GROUPS: dict[str, str] = {
    "activity": "steps steps_detail steps_stat sport_stat sport_record sport_file sport_goal exercise_intensity tumble sedentary app_usage app_usage_detail",
    "sleep": "sleep sleep_stat sleep_status sleep_index sleep_rr sleep_advice sleep_hr_stat snore_osa_detail snore_osa_sum snore_env_noise snore_feature snore_stat sensor_osa breath_rate",
    "cardiovascular": "heart_rate heart_rate_detail heart_rate_stat heart_rate_warning blood_oxygen blood_oxygen_detail blood_oxygen_stat blood_oxygen_warning blood_oxygen_origin blood_pressure blood_pressure_stat atrial_fibril atrial_fibril_warning cardiovascular ecg hrv",
    "body": "weight weight_target wrist_temperature wrist_temperature_detail blood_sugar blood_sugar_stat blood_sugar_warning vitamin",
    "wellbeing": "physical_mental_detail physical_mental_stat physical_mental_achievement hearing hearing_stat cervical_stat cervical_action insight",
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
    "physical_mental_detail": "身心健康明细",
    "physical_mental_stat": "身心健康统计", "physical_mental_achievement": "身心健康成就", "hearing": "听力记录",
    "hearing_stat": "听力统计", "cervical_stat": "颈椎训练统计", "cervical_action": "颈椎训练动作", "insight": "健康洞察",
    "physiological_cycle": "生理周期", "physiological_symptoms": "生理症状", "ovulation": "排卵信息",
    "sunshine": "日照记录", "sunshine_stat": "日照统计", "physical_mental": "身心健康组合数据",
}

# These overrides make the dashboard say what a record actually represents instead of
# calling every non-scalar payload an opaque "event".
KIND_BY_KEY: dict[str, str] = {
    "steps_detail": "interval", "sport_record": "snapshot", "sport_file": "snapshot", "sport_goal": "snapshot",
    "tumble": "event", "sedentary": "interval", "app_usage_detail": "interval",
    "sleep_status": "interval", "sleep_index": "index", "sleep_rr": "interval", "sleep_advice": "snapshot",
    "snore_osa_detail": "interval", "snore_osa_sum": "daily_summary", "snore_env_noise": "interval",
    "snore_feature": "interval", "sensor_osa": "interval", "breath_rate": "observation",
    "heart_rate_warning": "event", "blood_oxygen_warning": "event", "blood_pressure": "observation",
    "atrial_fibril": "event", "atrial_fibril_warning": "event", "cardiovascular": "index", "ecg": "observation",
    "weight_target": "snapshot", "blood_sugar_warning": "event", "vitamin": "observation",
    "physical_mental_detail": "observation",
    "physical_mental_achievement": "snapshot", "hearing": "observation", "cervical_action": "interval",
    "insight": "snapshot", "physiological_cycle": "interval", "physiological_symptoms": "event",
    "ovulation": "event", "sunshine": "interval",
}

DESCRIPTIONS: dict[str, str] = {
    "steps": "按时间记录的步数或活动数据；可能同时包含距离、消耗和活动类型。",
    "steps_detail": "一次步行/活动区间的明细，重点查看开始时间、结束时间和区间内步数。",
    "steps_stat": "按天汇总的步数记录；总步数、距离、消耗和目标状态都在详情字段中。",
    "sport_stat": "按日期和运动模式汇总的运动数据，不代表一条完整运动轨迹。",
    "sport_record": "云端运动记录的版本索引，只说明有一份记录发生过变化，不是解密后的轨迹。",
    "sport_file": "加密运动文件记录；当前缺少应用运行时密钥时不会展示为可读轨迹。",
    "sport_goal": "运动目标或目标快照，例如步数、消耗和锻炼时长目标。",
    "exercise_intensity": "按天或时间段汇总的运动强度等级和持续时间。",
    "tumble": "跌倒检测或跌倒事件记录，详情中查看事件类型、时间和源端状态。",
    "sedentary": "久坐提醒及其时间段，不是久坐时长的单一测量值。",
    "app_usage": "按日期汇总的应用使用情况，包名/应用类型和时长分别保留。",
    "app_usage_detail": "某个应用的一次使用区间，详情中查看包名、起止时间和时长。",
    "sleep": "一晚睡眠的日期汇总，包含总睡眠、睡眠分期或睡眠上下文。",
    "sleep_stat": "按天汇总的睡眠统计，入睡/起床时间、分期和评分分别展示。",
    "sleep_status": "睡眠阶段区间记录，例如清醒、浅睡、深睡或 REM 阶段。",
    "sleep_index": "源端睡眠指数或评分，只展示原始分数和字段，不作医学解释。",
    "sleep_rr": "睡眠期间的 RR 间隔或序列数据，可能是区间内的多点结构。",
    "sleep_advice": "云端生成的睡眠建议文本或建议事件，原文会在详情中保留。",
    "sleep_hr_stat": "一晚睡眠期间的心率统计，多项统计字段不会合并成一个值。",
    "snore_osa_detail": "鼾声/OSA 模型的一段区间或分类结果，保留模型字段和时间范围。",
    "snore_osa_sum": "一晚或一天的 OSA 模型汇总，不等同于临床诊断。",
    "snore_env_noise": "睡眠环境噪声的区间或采样记录，单位以源端字段为准。",
    "snore_feature": "鼾声特征的区间记录，详情中查看特征结构和时间范围。",
    "snore_stat": "鼾声次数、时长或分类等统计字段的汇总记录。",
    "sensor_osa": "OSA 传感器的原始区间/事件数据，保留源端类别和设备字段。",
    "breath_rate": "区间内逐点呼吸率采样；每个点可点击查看具体读数和时间。",
    "heart_rate": "一次心率测量或采样，类型、设备和测量上下文保留在详情中。",
    "heart_rate_detail": "版本化的心率采样明细，包含测量类型和源端修订时间。",
    "heart_rate_stat": "按天汇总的心率统计，均值、分位等字段按源端原样保留。",
    "heart_rate_warning": "心率预警事件，详情中查看触发时间、类型和源端阈值。",
    "blood_oxygen": "一次血氧测量或采样，饱和度和测量上下文分开保留。",
    "blood_oxygen_detail": "版本化的血氧采样明细，保留测量类型和修改时间。",
    "blood_oxygen_stat": "按天汇总的血氧统计，数值和范围字段不会被压成单一结论。",
    "blood_oxygen_warning": "血氧预警事件，而不是连续血氧样本。",
    "blood_oxygen_origin": "血氧设备返回的原始记录，保留设备来源和未转换字段。",
    "blood_pressure": "一次血压测量事件，收缩压、舒张压和上下文分别在详情中显示。",
    "blood_pressure_stat": "按天或周期汇总的血压统计，保留源端统计口径。",
    "atrial_fibril": "房颤筛查记录，详情中查看源端分类和结论文本，不作医学诊断。",
    "atrial_fibril_warning": "房颤预警事件，与筛查测量分开保存。",
    "cardiovascular": "心血管评估或指数记录，多个评估项在结构化详情中展示。",
    "ecg": "心电图记录元数据或测量事件；当前未把未接入的波形伪装成已获取数据。",
    "hrv": "心率变异性测量或时间序列记录，详情中保留原始 HRV 字段。",
    "weight": "一次体重测量；数值型记录会显示为 kg，其他源字段仍在详情中保留。",
    "weight_target": "体重目标及其生效区间，是目标快照而不是测量记录。",
    "wrist_temperature": "按天汇总的腕温相对个人基线变化，不是体温计测得的绝对体温。",
    "wrist_temperature_detail": "一次腕温或相对变化记录，详情中保留源端温度语义。",
    "blood_sugar": "一次血糖测量，餐前/餐后和单位等上下文在详情中保留。",
    "blood_sugar_stat": "按天或周期汇总的血糖统计，测量类型和统计口径分字段展示。",
    "blood_sugar_warning": "血糖预警事件，不据此生成疾病判断。",
    "vitamin": "维生素或相关健康指标记录，指标、数值和单位按源端字段保存。",
    "physical_mental_detail": "一条复合身心健康测量/训练记录，可能同时包含身心健康值、HRV、评分和时间偏移；它不是一个单值。",
    "physical_mental_stat": "按天汇总的身心健康统计，身心健康值、状态和其他指标字段保留在详情中。",
    "physical_mental_achievement": "身心健康目标/成就快照，记录日期、目标内容和达成状态；如含分数，按身心健康值区间解释。",
    "hearing": "一次听力测试记录，测试结果、耳别、频段或评分等字段在详情中分别显示。",
    "hearing_stat": "听力测试的统计记录，与单次听力测试分开保存。",
    "cervical_stat": "颈椎训练的日/周期汇总，例如次数、时长和完成状态。",
    "cervical_action": "一次颈椎训练动作或动作序列，保留动作、次数、持续时间和时间戳。",
    "insight": "云端健康洞察的原始复合快照，展示源端文本和字段，不做二次诊断推断。",
    "physiological_cycle": "生理周期的区间或阶段记录，日期语义按源端字段解释。",
    "physiological_symptoms": "生理症状事件，敏感字段只在认证后的明细中展示。",
    "ovulation": "源端排卵日期/周期事件，只展示已有信息，不额外推断。",
    "sunshine": "日照区间或日照记录，时长和强度使用源端单位。",
    "sunshine_stat": "按天汇总的日照统计，统计口径按源端字段保留。",
}

FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "physical_mental_detail": ("stressValue/stress 身心健康值（1-100，越高越好）", "heartRateVarValue/HRV", "startTimeOffset 时间偏移", "sourceType 来源类型"),
    "physical_mental_stat": ("date 日期", "score 身心健康值（1-100，越高越好）", "status 状态", "summary 汇总字段"),
    "physical_mental_achievement": ("date 日期", "score 身心健康值（1-100，越高越好）", "goal 目标", "achievement/达成状态"),
    "hearing": ("testTime 测试时间", "ear/耳别", "frequency/频段", "level/评分或分贝"),
    "hearing_stat": ("date 日期", "average/统计值", "left/right 左右耳", "status 状态"),
    "cervical_stat": ("date 日期", "duration 时长", "count 次数", "completion 完成状态"),
    "cervical_action": ("action 动作", "start/end 起止时间", "duration 持续时间", "count 次数"),
    "sleep_stat": ("sleepStart/sleepEnd 入睡起床", "deep/light/REM 分期", "score 指数/评分"),
    "sport_stat": ("sportMode 运动模式", "totalSteps 总步数", "totalDistance 总距离", "totalDuration 总时长"),
}

DAILY = {
    "steps_stat", "sport_stat", "sleep", "sleep_stat", "sleep_index", "sleep_hr_stat", "blood_oxygen_stat",
    "blood_pressure_stat", "blood_sugar_stat", "heart_rate_stat", "physical_mental_stat",
    "hearing_stat", "cervical_stat", "snore_stat", "sunshine_stat", "exercise_intensity",
}
OBSERVATIONS = {
    "heart_rate", "heart_rate_detail", "blood_oxygen", "blood_oxygen_detail", "breath_rate", "hrv", "weight",
    "blood_pressure", "blood_sugar", "wrist_temperature_detail",
}
UNITS = {
    "heart_rate": "bpm", "heart_rate_detail": "bpm", "heart_rate_stat": "bpm", "sleep_hr_stat": "bpm",
    "blood_oxygen": "%", "blood_oxygen_detail": "%", "steps": "步", "steps_detail": "步", "steps_stat": "步",
    "weight": "kg", "breath_rate": "次/分",
}
COMPOSITES = {"physical_mental": ("physical_mental_detail", "physical_mental_stat", "physical_mental_achievement")}


def build_catalog(endpoint_names: list[str] | tuple[str, ...]) -> dict[str, Metric]:
    category_by_key = {key: group for group, keys in GROUPS.items() for key in keys.split()}
    result: dict[str, Metric] = {}
    for key in endpoint_names:
        category = category_by_key.get(key, "other")
        kind = KIND_BY_KEY.get(key, "daily_summary" if key in DAILY else "observation" if key in OBSERVATIONS else "event")
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
        result[key] = Metric(
            key,
            NAMES.get(key, key.replace("_", " ").title()),
            category,
            kind,
            UNITS.get(key),
            caveat=caveat or "单位按字段级源定义确认；未知单位保持未指定",
            available=available,
            description=DESCRIPTIONS.get(key, f"{NAMES.get(key, key)}的源端结构化记录，完整字段请打开记录详情查看。"),
            field_hints=FIELD_HINTS.get(key, ()),
            value_context=deepcopy(PHYSICAL_MENTAL_VALUE_CONTEXT) if key in PHYSICAL_MENTAL_METRICS else None,
        )
    for key, sources in COMPOSITES.items():
        result[key] = Metric(
            key,
            NAMES[key],
            "wellbeing",
            "composite",
            None,
            "由子数据集汇总；不单独请求云端",
            "数据分组视图，不产生新的测量值",
            description=f"组合查看：{'、'.join(NAMES.get(source, source) for source in sources)}。点击具体子指标可查看原始记录详情。",
            field_hints=tuple(sources),
            value_context=deepcopy(PHYSICAL_MENTAL_VALUE_CONTEXT) if key == "physical_mental" else None,
        )
    return result


def category_names() -> dict[str, str]:
    return {
        "activity": "活动与运动",
        "sleep": "睡眠与恢复",
        "cardiovascular": "心血管",
        "body": "身体测量",
        "wellbeing": "健康与身心",
        "cycle_environment": "女性健康与环境",
        "other": "其他",
    }
