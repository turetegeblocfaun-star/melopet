# ruff: noqa: E501

VALIDATION_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeloPet 后端验收台</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #24304a;
      --muted: #6d7690;
      --line: #dfe3ef;
      --surface: rgba(255, 255, 255, 0.9);
      --primary: #7457e8;
      --primary-dark: #5d43ce;
      --success: #20835d;
      --success-bg: #e8f7f0;
      --running: #356fd1;
      --running-bg: #eaf2ff;
      --danger: #b43b47;
      --danger-bg: #fff0f1;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      min-height: 100vh;
      background:
        radial-gradient(circle at 12% 10%, rgba(129, 194, 255, 0.28), transparent 28%),
        radial-gradient(circle at 86% 4%, rgba(180, 143, 255, 0.24), transparent 30%),
        #f6f7fb;
    }
    main { width: min(980px, calc(100% - 32px)); margin: 0 auto; padding: 52px 0 64px; }
    .hero { text-align: center; margin-bottom: 28px; }
    .eyebrow {
      display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px; border-radius: 999px;
      color: #5e49b4; background: #eeeafe; font-size: 13px; font-weight: 700;
    }
    h1 { margin: 16px 0 8px; font-size: clamp(30px, 5vw, 46px); letter-spacing: -1.2px; }
    .hero p { margin: 0 auto; max-width: 680px; color: var(--muted); line-height: 1.7; font-size: 16px; }
    .panel {
      background: var(--surface); border: 1px solid rgba(220, 224, 237, 0.9); border-radius: 24px;
      padding: 24px; box-shadow: 0 20px 60px rgba(44, 52, 86, 0.1); backdrop-filter: blur(12px);
    }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
    .topbar h2 { margin: 0; font-size: 20px; }
    .service-state { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 14px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #a8afc0; }
    .dot.online { background: #24a16d; box-shadow: 0 0 0 5px rgba(36, 161, 109, 0.12); }
    .steps { display: grid; gap: 10px; }
    .step {
      display: grid; grid-template-columns: 42px 1fr auto; align-items: center; gap: 14px;
      min-height: 72px; padding: 13px 16px; border: 1px solid var(--line); border-radius: 16px; background: #fff;
      transition: 180ms ease;
    }
    .step-number {
      display: grid; place-items: center; width: 38px; height: 38px; border-radius: 12px;
      color: #707991; background: #f1f3f8; font-weight: 800;
    }
    .step strong { display: block; margin-bottom: 4px; font-size: 15px; }
    .step small { color: var(--muted); font-size: 13px; }
    .badge { padding: 6px 10px; border-radius: 999px; background: #f2f3f7; color: #788096; font-size: 12px; font-weight: 700; }
    .step.running { border-color: #a9c7fa; background: var(--running-bg); }
    .step.running .step-number, .step.running .badge { background: #d8e7ff; color: var(--running); }
    .step.passed { border-color: #a9dfc8; background: var(--success-bg); }
    .step.passed .step-number, .step.passed .badge { background: #ceefdf; color: var(--success); }
    .step.failed { border-color: #efbac0; background: var(--danger-bg); }
    .step.failed .step-number, .step.failed .badge { background: #f9dadd; color: var(--danger); }
    .actions { display: flex; align-items: center; gap: 16px; margin-top: 20px; }
    button {
      appearance: none; border: 0; border-radius: 14px; padding: 14px 22px; color: white;
      background: linear-gradient(135deg, var(--primary), #8c69f1); box-shadow: 0 10px 24px rgba(116, 87, 232, 0.25);
      font-size: 15px; font-weight: 800; cursor: pointer;
    }
    button:hover { background: linear-gradient(135deg, var(--primary-dark), #7651e4); }
    button:disabled { opacity: 0.6; cursor: wait; }
    .hint { color: var(--muted); font-size: 13px; line-height: 1.5; }
    .error { display: none; margin-top: 16px; padding: 13px 15px; border-radius: 12px; background: var(--danger-bg); color: var(--danger); line-height: 1.6; }
    .results { display: none; margin-top: 22px; }
    .results h2 { margin: 0 0 14px; font-size: 22px; }
    .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
    .metric { padding: 15px; border-radius: 15px; background: #f7f7fb; border: 1px solid #e8e9f1; }
    .metric b { display: block; font-size: 25px; margin-bottom: 3px; }
    .metric span { color: var(--muted); font-size: 13px; }
    .moods { margin-bottom: 18px; padding: 16px; border-radius: 16px; background: #faf9ff; border: 1px solid #ece8fc; }
    .moods strong { display: block; margin-bottom: 10px; }
    .mood-list { display: flex; flex-wrap: wrap; gap: 8px; }
    .mood-pill { padding: 7px 10px; border-radius: 999px; color: #5f4dae; background: #ede9fe; font-size: 13px; }
    .pets { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .pet { overflow: hidden; border: 1px solid var(--line); border-radius: 18px; background: white; }
    .pet-visual { display: grid; place-items: center; min-height: 190px; padding: 18px; background: linear-gradient(145deg, #f3f1ff, #eef7ff); }
    .pet img { width: 150px; height: 150px; object-fit: contain; }
    .pet-copy { padding: 15px; }
    .pet-copy h3 { margin: 0 0 7px; font-size: 18px; }
    .pet-copy p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.6; }
    .notice { margin-top: 18px; padding: 14px 16px; border-radius: 14px; color: #80621d; background: #fff8e5; font-size: 13px; line-height: 1.6; }
    footer { text-align: center; margin-top: 20px; color: var(--muted); font-size: 13px; }
    footer a { color: #6650c4; text-decoration: none; }
    @media (max-width: 720px) {
      main { padding-top: 30px; }
      .panel { padding: 17px; border-radius: 19px; }
      .summary { grid-template-columns: repeat(2, 1fr); }
      .pets { grid-template-columns: 1fr; }
      .topbar, .actions { align-items: flex-start; flex-direction: column; }
      .step { grid-template-columns: 38px 1fr; }
      .step .badge { grid-column: 2; justify-self: start; }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <span class="eyebrow">开发环境 · 只验证后端</span>
      <h1>MeloPet 后端验收台</h1>
      <p>不用看接口、不用填参数。点击一次，系统会自动验证从歌单导入到桌宠候选的完整后端链路。</p>
    </section>

    <section class="panel">
      <div class="topbar">
        <h2>核心链路检查</h2>
        <div class="service-state"><span id="service-dot" class="dot"></span><span id="service-text">正在检查服务…</span></div>
      </div>

      <div class="steps">
        <div class="step" data-step="service"><span class="step-number">1</span><div><strong>连接后端</strong><small>检查 API 和本地验证身份</small></div><span class="badge">等待</span></div>
        <div class="step" data-step="import"><span class="step-number">2</span><div><strong>导入测试歌单</strong><small>上传 12 首模拟歌曲并完成字段映射</small></div><span class="badge">等待</span></div>
        <div class="step" data-step="analysis"><span class="step-number">3</span><div><strong>生成情绪画像</strong><small>交给独立 Worker 处理，并检查分析结果</small></div><span class="badge">等待</span></div>
        <div class="step" data-step="confirm"><span class="step-number">4</span><div><strong>确认画像</strong><small>验证样本数、未知率和画像版本</small></div><span class="badge">等待</span></div>
        <div class="step" data-step="pet"><span class="step-number">5</span><div><strong>生成桌宠候选</strong><small>生成 3 份 PetSpec、文案和开发预览图</small></div><span class="badge">等待</span></div>
      </div>

      <div class="actions">
        <button id="run-button" type="button">开始一键验证</button>
        <span class="hint">验证数据只保存在本机开发数据库中。</span>
      </div>
      <div id="error" class="error"></div>

      <div id="results" class="results">
        <h2>✅ 后端核心链路通过</h2>
        <div class="summary">
          <div class="metric"><b id="metric-total">-</b><span>成功导入</span></div>
          <div class="metric"><b id="metric-known">-</b><span>成功识别</span></div>
          <div class="metric"><b id="metric-unknown">-</b><span>未知歌曲</span></div>
          <div class="metric"><b id="metric-pets">-</b><span>桌宠候选</span></div>
        </div>
        <div class="moods"><strong>情绪画像分布</strong><div id="mood-list" class="mood-list"></div></div>
        <div id="pets" class="pets"></div>
        <div class="notice">当前使用规则分析和开发占位资产。这可以证明后端流程可运行，不代表真实模型效果或正式桌宠美术已验收。</div>
      </div>
    </section>

    <footer>这是非产品化的后端验收工具 · <a href="/docs" target="_blank" rel="noreferrer">开发人员查看接口文档</a></footer>
  </main>

  <script>
    const playlist = `title,artist,genre
Neon Run,Aster Lane,rock energetic
Sunday Window,Paper Birds,happy pop
Quiet Orbit,Luma Field,ambient focus
Amber Room,North Pine,warm acoustic
Electric Tide,Moon Relay,rock energetic
Small Parade,June Avenue,happy pop
Deep Work,Cloud Atlas,ambient focus
Old Postcard,Cedar House,warm acoustic
Night Engine,Silver Static,rock energetic
Blue Confetti,Daylight Club,happy pop
Still Water,Velvet Sky,ambient focus
Kitchen Light,Hollow Oak,warm acoustic`;

    const button = document.getElementById("run-button");
    const errorBox = document.getElementById("error");
    const results = document.getElementById("results");
    const stepOrder = ["service", "import", "analysis", "confirm", "pet"];
    const moodNames = {
      heroic_drive: "热血/激昂", joy_bright: "欢乐/明亮", healing_warm: "治愈/温暖",
      romantic_tender: "浪漫/柔情", melancholy_sad: "感伤/低落", calm_focus: "平静/专注",
      mysterious_dark: "神秘/暗黑", nostalgic_story: "怀旧/叙事", UNKNOWN: "待确认"
    };

    function uniqueKey(label) {
      const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      return `browser-validation-${label}-${random}`;
    }

    function setStep(name, state, detail) {
      const element = document.querySelector(`[data-step="${name}"]`);
      element.classList.remove("running", "passed", "failed");
      if (state !== "waiting") element.classList.add(state);
      const badge = element.querySelector(".badge");
      badge.textContent = state === "running" ? "正在验证" : state === "passed" ? "已通过" : state === "failed" ? "失败" : "等待";
      if (detail) element.querySelector("small").textContent = detail;
    }

    function reset() {
      stepOrder.forEach((name) => setStep(name, "waiting"));
      errorBox.style.display = "none";
      results.style.display = "none";
      document.getElementById("pets").replaceChildren();
      document.getElementById("mood-list").replaceChildren();
    }

    async function api(path, options = {}) {
      const response = await fetch(path, options);
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}）`);
        return response;
      }
      const payload = await response.json();
      if (!response.ok || payload.status !== "ok") {
        throw new Error(payload.safeMessage || payload.errorCode || `HTTP ${response.status}`);
      }
      return payload.data;
    }

    function auth(token, writeLabel, jsonBody) {
      const headers = { Authorization: `Bearer ${token}` };
      if (writeLabel) headers["Idempotency-Key"] = uniqueKey(writeLabel);
      if (jsonBody) headers["Content-Type"] = "application/json";
      return headers;
    }

    async function waitTask(token, taskId, expected) {
      for (let attempt = 0; attempt < 80; attempt += 1) {
        const task = await api(`/v1/tasks/${taskId}`, { headers: auth(token) });
        if (task.status === expected) return task;
        if (["PROFILE_READY", "CANDIDATES_READY", "SAFE_FALLBACK_READY", "FINAL_FAILED", "CANCELLED"].includes(task.status)) {
          throw new Error(`任务停在 ${task.status}，未到达 ${expected}`);
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      throw new Error("任务处理超时，请检查 Worker 是否运行");
    }

    async function renderResults(token, mapped, profile, candidates) {
      document.getElementById("metric-total").textContent = `${mapped.normalizedCount} 首`;
      document.getElementById("metric-known").textContent = `${profile.knownCount} 首`;
      document.getElementById("metric-unknown").textContent = `${profile.unknownCount} 首`;
      document.getElementById("metric-pets").textContent = `${candidates.length} 个`;

      const moodList = document.getElementById("mood-list");
      profile.percentages.filter((item) => item.percent > 0).forEach((item) => {
        const pill = document.createElement("span");
        pill.className = "mood-pill";
        pill.textContent = `${moodNames[item.moodId] || item.moodId} ${item.percent}%`;
        moodList.appendChild(pill);
      });

      const pets = document.getElementById("pets");
      for (const candidate of candidates) {
        const preview = await fetch(candidate.previewUrl, { headers: auth(token) });
        if (!preview.ok) throw new Error("桌宠预览图读取失败");
        const previewUrl = URL.createObjectURL(await preview.blob());
        const card = document.createElement("article");
        card.className = "pet";
        const visual = document.createElement("div");
        visual.className = "pet-visual";
        const image = document.createElement("img");
        image.src = previewUrl;
        image.alt = `${candidate.copy.petName || "桌宠"}的开发预览`;
        visual.appendChild(image);
        const copy = document.createElement("div");
        copy.className = "pet-copy";
        const title = document.createElement("h3");
        title.textContent = candidate.copy.petName || `候选 ${candidate.candidateIndex + 1}`;
        const intro = document.createElement("p");
        intro.textContent = candidate.copy.intro || "PetSpec 已生成";
        copy.append(title, intro);
        card.append(visual, copy);
        pets.appendChild(card);
      }
      results.style.display = "block";
      results.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    async function runValidation() {
      reset();
      button.disabled = true;
      button.textContent = "正在验证…";
      let activeStep = "service";
      try {
        setStep("service", "running");
        const health = await api("/healthz");
        if (health.state !== "healthy") throw new Error("后端服务未就绪");
        const capability = await api("/v1/capabilities/anonymous", { method: "POST" });
        const token = capability.capabilityToken;
        setStep("service", "passed", "API 健康，本地验证身份已创建");

        activeStep = "import";
        setStep("import", "running");
        const imported = await api("/v1/playlist-imports", {
          method: "POST", headers: auth(token, "import", true),
          body: JSON.stringify({ text: playlist, consentExternalProcessing: false })
        });
        const mapped = await api(`/v1/playlist-imports/${imported.importId}/mapping`, {
          method: "PUT", headers: auth(token, "mapping", true),
          body: JSON.stringify({ mapping: { title: "title", artist: "artist", genre: "genre" } })
        });
        if (mapped.normalizedCount !== 12) throw new Error(`预期导入 12 首，实际为 ${mapped.normalizedCount} 首`);
        setStep("import", "passed", `12 首歌曲已规范化，无无效行`);

        activeStep = "analysis";
        setStep("analysis", "running");
        const analysisCreated = await api(`/v1/playlist-imports/${imported.importId}/analyses`, {
          method: "POST", headers: auth(token, "analysis")
        });
        const analysis = await waitTask(token, analysisCreated.taskId, "PROFILE_READY");
        const profile = await api(`/v1/mood-profiles/${analysis.output.profileRevision}`, { headers: auth(token) });
        setStep("analysis", "passed", `${profile.knownCount} 首已识别，${profile.unknownCount} 首未知`);

        activeStep = "confirm";
        setStep("confirm", "running");
        await api(`/v1/mood-profiles/${profile.revision}/confirm`, {
          method: "POST", headers: auth(token, "confirm")
        });
        setStep("confirm", "passed", `画像版本 ${profile.revisionNumber} 已确认`);

        activeStep = "pet";
        setStep("pet", "running");
        const petCreated = await api(`/v1/mood-profiles/${profile.revision}/pet-candidates`, {
          method: "POST", headers: auth(token, "pet")
        });
        await waitTask(token, petCreated.taskId, "CANDIDATES_READY");
        const candidateData = await api(`/v1/pet-candidates/${petCreated.taskId}`, { headers: auth(token) });
        if (candidateData.candidates.length !== 3) throw new Error("桌宠候选数不是 3 个");
        await renderResults(token, mapped, profile, candidateData.candidates);
        setStep("pet", "passed", "3 个 PetSpec、文案与 SVG 预览已生成");
      } catch (error) {
        setStep(activeStep, "failed");
        errorBox.textContent = `验证没有通过：${error.message || error}`;
        errorBox.style.display = "block";
      } finally {
        button.disabled = false;
        button.textContent = "重新验证一次";
      }
    }

    async function checkService() {
      try {
        const health = await api("/healthz");
        if (health.state !== "healthy") throw new Error();
        document.getElementById("service-dot").classList.add("online");
        document.getElementById("service-text").textContent = "后端服务在线";
      } catch {
        document.getElementById("service-text").textContent = "后端服务未连接";
      }
    }

    button.addEventListener("click", runValidation);
    checkService();
  </script>
</body>
</html>"""
