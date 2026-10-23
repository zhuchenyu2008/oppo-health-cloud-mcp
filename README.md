# oppo-health-cloud-mcp

OPPO 健康（欢太健康）云端 API 直连客户端 + MCP server。无需 root / LSPosed，从云端拉取你自己的健康明细数据（心率/血氧/步数/睡眠/体重/腕温/HRV/身心健康/呼吸率等），供任意 AI agent 分析。

> ⚠️ **仅供本人学习研究**。导出的是你自己的健康数据，请勿用于侵犯他人隐私或商用。

## 原理

OPPO 健康云 API（`sport.health.heytapmobi.com/sporthealth/`）的请求签名是 **HMAC-SHA256 + 静态密钥**：

```
canonical = sorted(k=v&)[:-1] + body去空白
signature = base64(HmacSHA256(httpSecret, canonical))
httpSecret = XXTEA解密(BuildConfig.httpSecret常量, 两个UUID逐字符交错)
```

密钥是 APK `BuildConfig` 里的**静态常量**（非服务端协商），因此一次逆向、永久复刻，无需真机签名代理。50/50 抓包对拍验证通过。

## 服务器中转平台（当前入口）

仓库已新增单用户 Web 看板、SQLite 标准化缓存、后台调度器和远程只读 MCP。服务器运行 `oppo_health.app:app`；旧 `cloud_sync`、`cloud_query`、`cloud_analyze`、`cloud_status` 不再注册为 MCP 工具。`cloud_mcp.py` 保留经真实联调验证的 OPPO curl/签名客户端，并且直接执行时启动新中转应用，不再启动旧 stdio MCP。

实施架构和验收标准见[实施落地方案](docs/实施落地方案.md)。

### 本机开发

1. 按[国内版 OPPO 健康认证手册](OPPO健康国内版云端MCP认证手册.md)获取认证信息，并在本机准备 `auth.json`（不要提交）：
   ```json
   {"token": "ACCESS_...", "token_auth_id": "...", "ssoid": "..."}
   ```
2. 安装开发依赖：
   ```powershell
   python -m pip install -e ".[test]"
   ```
3. 生成 Argon2 密码哈希：
   ```powershell
   python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('替换为自己的强密码'))"
   ```
4. 复制 `config.example.toml` 为 `config.toml`，配置 `security.password_hash`；本机开发时设置 `APP_ENV=development`、`OPPO_AUTH_FILE` 指向 `auth.json`，然后运行：
   ```powershell
   $env:PYTHONPATH = "src;."
   uvicorn oppo_health.app:app --host 127.0.0.1 --port 8000
   ```
5. 浏览器打开 `http://127.0.0.1:8000`。第一次运行会回填最多 365 天，后续按配置默认每 10 分钟同步。没有可用 OPPO 认证时应用仍可启动，但同步状态会显示“等待 OPPO 认证”。

### Docker Compose 部署

1. 复制 `config.example.toml` 为 `deploy/config.toml`，填写真实公开 HTTPS 地址；复制 `deploy/.env.example` 为 `deploy/.env` 并填写网页登录密码哈希和 OAuth issuer。
2. 在 `deploy/secrets/auth.json` 放置 OPPO 认证 JSON；该目录和 `.env` 均被 Git 忽略。
3. 在 `deploy` 目录执行 `docker compose up -d --build`。应用只绑定服务器回环地址 `127.0.0.1:8000`；按 `deploy/Caddyfile.example` 配置服务器反向代理和 HTTPS。
4. 验证 `/healthz`、`/readyz`、网页登录以及 `/mcp/` OAuth 授权。远程 MCP 只提供 `get_health_overview`、`query_health_data`、`list_health_metrics`、`get_sync_status` 四个只读工具；手动补同步只能通过已登录网页发起。

本仓库提交不包含真实服务器凭据、域名、证书或健康数据。本次仅完成软件和部署样例，未连接 `172.245.39.168`、未部署、未做真实账号上游联调。

`wrist_temperature` 返回腕部温度统计。数值表示相对个人基线的变化，不是体温计测得的绝对体温。

### 新增健康指标

| 指标 | MCP metric | 缓存内容 |
|---|---|---|
| 心率明细 | `heart_rate_detail` | `heartRateValue`、`heartRateType`、`dataCreatedTimestamp`、`modifiedTimestamp` 等；先走版本列表，再按版本读取 |
| 心率统计 | `heart_rate_stat` | `pullStatVersionPageData` 返回的日统计；账号没有统计记录时返回空集 |
| 步数明细 | `steps_detail` | `steps`、`calories`、`distance`、`sportMode`、`startTimestamp`、`endTimestamp`、`modifiedTime` 等 |
| 步数统计 | `steps_stat` | `totalSteps`、`totalDistance`、`totalCalories`、`totalDuration`、`date`、目标完成状态等 |
| 睡眠状态 | `sleep_status` | `sleepState`、`sleepType`、`startTimestamp`、`endTimestamp`、`dataVersion` 等睡眠阶段记录 |
| 睡眠统计 | `sleep_stat` | `totalSleepTime`、`totalDeepSleepTime`、`totalLightlySleepTime`、`totalRemTime`、`totalWakeUpTime`、入睡/起床时间和睡眠评分 |
| 睡眠指数 | `sleep_index` | `sleepRecoveryRate`、`sleepRecoveryDiffValue`、`avgSleepSpo2`、睡眠/呼吸/心率合理范围等 |
| 睡眠 RR 间隔 | `sleep_rr` | `rrIntervalDataList`、`startTimestamp`、`modifiedTimestamp` |
| HRV | `hrv` | 心率变异性明细，包含 `heartRateVarValue`、`startTimestamp`、`endTimestamp` 等字段 |
| 身心健康 | `physical_mental` | `physical_mental_detail` 明细、`physical_mental_stat` 日统计、`physical_mental_achievement` 每日目标与成就 |
| 呼吸率 | `breath_rate` | 呼吸率记录，外层按时间段保存，逐条读数位于嵌套 `dataList`（含 `respiratoryRate`） |
| 血氧扩展 | `blood_oxygen_detail` / `blood_oxygen_stat` / `blood_oxygen_warning` / `blood_oxygen_origin` | 血氧明细、日统计、预警和设备原始记录 |
| 血压 | `blood_pressure` / `blood_pressure_stat` | 血压明细和统计 |
| 血糖 | `blood_sugar` / `blood_sugar_stat` / `blood_sugar_warning` | 血糖明细、统计和预警 |
| 心率预警 | `heart_rate_warning` | 心率预警明细 |
| 房颤 | `atrial_fibril` / `atrial_fibril_warning` | 房颤明细和预警 |
| 心血管评估 | `cardiovascular` | 心血管评估记录 |
| 久坐 | `sedentary` | 久坐提醒记录 |
| 睡眠扩展 | `sleep_advice` / `sleep_hr_stat` | 睡眠建议和睡眠心率统计 |
| 腕温明细 | `wrist_temperature_detail` | 腕温明细记录；`wrist_temperature` 是统计记录 |
| 心电图 | `ecg` | 心电图记录元数据；波形文件可能仍需单独下载/解密 |
| 打鼾/OSA | `snore_osa_detail` / `snore_osa_sum` / `snore_env_noise` / `snore_feature` / `sensor_osa` | OSA 模型、汇总、环境噪声、鼾声特征和传感器记录 |
| 鼾声统计 | `snore_stat` | 鼾声统计记录 |
| 运动强度 | `exercise_intensity` | 运动强度统计 |
| 体重目标 | `weight_target` | 体重目标统计 |
| 维生素 | `vitamin` | 维生素记录 |
| 女性生理 | `physiological_cycle` / `physiological_symptoms` / `ovulation` | 周期、症状和排卵信息 |
| 听力健康 | `hearing` | 听力测试记录 |
| 颈椎训练统计 | `cervical_stat` | 颈椎训练统计记录 |
| 颈椎训练动作 | `cervical_action` | 颈椎训练动作明细 |
| 日照 | `sunshine` | 日照明细记录 |
| 运动统计 | `sport_stat` | `sportMode`、`totalSteps`、`totalDistance`、`totalCalories`、`totalDuration`、`date` 等 |
| 运动记录版本 | `sport_record` | 云端运动文件的 `modifiedTime` 版本索引；它不是已经解密的轨迹明细 |
| 运动目标 | `sport_goal` | 步数、消耗、锻炼时长等目标的类型、单位、数值和生效时间 |
| 应用使用明细 | `app_usage_detail` | 按日期返回应用包的使用区间和使用时长；和 `app_usage` 的日统计分开 |

`cloud_sync` 的 `days` 对版本游标指标表示从最近日期开始筛选记录。心率/步数/睡眠明细、HRV 和呼吸率先请求对应的版本接口，再按 `modifiedTimestamp`（步数为 `modifiedTime`）查询正文；统计接口使用对应的 `pullStatVersionPageData` 或版本查询接口。

新增指标均来自 APK 中声明的云端读取服务，MCP 按日拉取或按版本游标读取并缓存。接口存在不代表账号一定有该类数据；成功返回空集只说明本次没有记录。当前还未覆盖 APK 中全部服务接口。

APK 还声明了健康档案、健康指标、疾病风险和复诊计划接口，但这些接口使用 `aihealth-api-cn.heytapmobi.com` 独立域名及 AES-GCM 会话密钥；当前 MCP 的 sport-health 明文传输层尚未接入这套密钥交换，因此不把它们注册为可用 metric。

截至 2026-10-20，已对多个新增接口完成真实账号联调；具体返回取决于账号、设备和历史数据。本文不包含个人健康记录数量或读数。

`sport_file` 对应云端加密运动文件，需要应用运行时生成的 `encryptKeyEnc`。当前 MCP 尚未生成该运行时密钥，因此调用会返回 `encrypt_key_enc_required`，不会把版本索引误当成运动轨迹数据。

为避免独立压力数据与身心健康值混淆，压力明细和压力统计不再注册到 MCP/网页指标目录，也不会继续同步；历史缓存保留在本地数据库中，但查询接口不会返回。身心健康明细、统计和成就仍正常同步并附带“数值越高越好”的字段语义。若 HRV 独立接口没有记录，MCP 会从身心健康明细的 `hrv` 子项生成对应 HRV 记录，并标注 `source: physical_mental_detail`，同时保留原始 `startTimeOffset`。

## 踩坑记录（重要）

| 坑 | 结论 |
|---|---|
| python urllib/requests 全部 403 `10103` | **TLS 指纹拦截**。必须 curl 子进程（okhttp 指纹相近），真假 token 差分法会失效 |
| 加了 `virtual-ssoid` 头 / gzip 头 → `10100` | **多余头参与验签**。只发精确 HAR 头集，ssoid 走 body 的 userTagId |
| v5 pull* 端点回 `23103` | 单次范围 ≤1 天，按天循环 |
| steps 响应是 dict，心率/血氧是数组 | 解析需兼容两种结构 |

错误码分层：`10103`=网关层(TLS/头)，`10100`=验签层(头污染)，`23103`=业务层(范围)，`0`=成功。

## License

MIT
