// Site configuration for the chat widget — edit this file to brand the
// frontend for your site. It must be loaded BEFORE chatbot.js (see
// chatbot_iframe.html). The backend has its own sibling file, site_config.py
// at the repo root.
window.SITE_CONFIG = {
    // Shown in welcome text via the {site} placeholder below.
    siteName: "Example Site",

    // Contact address shown in the welcome screen and the contact banner.
    contactEmail: "info@example-site.org",

    // Base URL of the Flask chat server. "" means same origin as the page
    // serving chatbot_iframe.html; set e.g. "https://chatbot.example-site.org"
    // when the widget is hosted separately from the API.
    apiBase: "",

    // Namespace for localStorage keys (session id + saved chat history).
    // Change it if you run several chatbots on one domain.
    storagePrefix: "site_chatbot",

    // Welcome-screen text for the language selector. {site} is replaced with
    // siteName at render time. Add or remove languages here AND in the
    // <select id="lang-select"> in chatbot_iframe.html.
    welcomeTranslations: {
        English:    { greeting: "Hi!",      intro: "I'm the {site} chatbot.",                            question: "What is your question?",      contactPre: "Please contact",                     contactPost: "if your question is not answered.",      disclaimer: "AI generated content. Always double check sources." },
        French:     { greeting: "Salut !",  intro: "Je suis le chatbot de {site}.",                      question: "Quelle est votre question ?", contactPre: "Veuillez contacter",                 contactPost: "si votre question n'a pas de réponse.",  disclaimer: "Contenu généré par l'IA. Vérifiez toujours les sources." },
        Spanish:    { greeting: "¡Hola!",   intro: "Soy el chatbot de {site}.",                          question: "¿Cuál es tu pregunta?",        contactPre: "Por favor contacte",                 contactPost: "si su pregunta no ha sido respondida.",  disclaimer: "Contenido generado por IA. Verifica siempre las fuentes." },
        Arabic:     { greeting: "مرحباً!", intro: "أنا روبوت المحادثة الخاص بـ {site}.",                question: "ما سؤالك؟",                    contactPre: "يرجى التواصل مع",                    contactPost: "إذا لم تتم الإجابة على سؤالك.",           disclaimer: "محتوى من إنشاء الذكاء الاصطناعي. تحقق دائمًا من المصادر." },
        Chinese:    { greeting: "你好！",   intro: "我是{site}的聊天机器人。",                            question: "您有什么问题？",                contactPre: "如果您的问题未得到解答，请联系",      contactPost: "。",                                      disclaimer: "AI 生成的内容。请始终核对来源。" },
        Urdu:       { greeting: "ہیلو!",    intro: "میں {site} کا چیٹ بوٹ ہوں۔",                        question: "آپ کا سوال کیا ہے؟",           contactPre: "اگر آپ کے سوال کا جواب نہیں ملا تو", contactPost: "سے رابطہ کریں۔",                          disclaimer: "اے آئی سے تیار کردہ مواد۔ ہمیشہ ذرائع کی تصدیق کریں۔" },
        Portuguese: { greeting: "Olá!",     intro: "Sou o chatbot de {site}.",                           question: "Qual é a sua pergunta?",       contactPre: "Por favor contacte",                 contactPost: "se a sua pergunta não foi respondida.",  disclaimer: "Conteúdo gerado por IA. Verifique sempre as fontes." },
    },

    // BCP-47 tags for browser speech recognition, keyed by the same language
    // names as welcomeTranslations. The language selector stores names, but the
    // Web Speech API wants a tag, so this is the bridge. A language with no
    // entry here falls back to en-US. Regional variants are a judgement call —
    // swap pt-PT for pt-BR, ar-SA for ar-EG, etc. to match your audience.
    speechLangs: {
        English:    "en-US",
        French:     "fr-FR",
        Spanish:    "es-ES",
        Arabic:     "ar-SA",
        Chinese:    "zh-CN",
        Urdu:       "ur-PK",
        Portuguese: "pt-PT",
    },

    // Voice-input UI strings. `privacy` is shown once, the first time someone
    // starts dictation: browser speech recognition may send audio to the
    // browser vendor (Chrome does; Safari recognizes on-device), and that is
    // worth disclosing since it is not this project's backend.
    voiceTranslations: {
        English:    { start: "Speak your question",     stop: "Stop dictation",     listening: "Listening…",        denied: "Microphone access is blocked. Enable it in your browser settings.",      noSpeech: "I didn't catch that. Try again.",       noMic: "No microphone found.",              network: "Speech recognition is unavailable right now.",     other: "Dictation stopped unexpectedly.",     privacy: "Speech is processed by your browser, not by this site." },
        French:     { start: "Dictez votre question",   stop: "Arrêter la dictée",  listening: "J'écoute…",         denied: "L'accès au microphone est bloqué. Activez-le dans les paramètres.",      noSpeech: "Je n'ai pas entendu. Réessayez.",       noMic: "Aucun microphone détecté.",         network: "La reconnaissance vocale est indisponible.",        other: "La dictée s'est arrêtée.",            privacy: "La voix est traitée par votre navigateur, pas par ce site." },
        Spanish:    { start: "Dicta tu pregunta",       stop: "Detener dictado",    listening: "Escuchando…",       denied: "El acceso al micrófono está bloqueado. Actívalo en los ajustes.",        noSpeech: "No te escuché. Inténtalo de nuevo.",    noMic: "No se encontró micrófono.",         network: "El reconocimiento de voz no está disponible.",      other: "El dictado se detuvo.",               privacy: "La voz la procesa tu navegador, no este sitio." },
        Arabic:     { start: "أملِ سؤالك",              stop: "إيقاف الإملاء",      listening: "أستمع…",            denied: "الوصول إلى الميكروفون محظور. فعّله من إعدادات المتصفح.",                  noSpeech: "لم أسمع ذلك. حاول مرة أخرى.",           noMic: "لم يتم العثور على ميكروفون.",       network: "التعرف على الصوت غير متاح حالياً.",                 other: "توقف الإملاء بشكل غير متوقع.",        privacy: "تتم معالجة الصوت بواسطة متصفحك، وليس هذا الموقع." },
        Chinese:    { start: "语音输入问题",              stop: "停止语音输入",         listening: "正在聆听…",          denied: "麦克风访问被阻止。请在浏览器设置中启用。",                                    noSpeech: "没有听清，请再试一次。",                  noMic: "未找到麦克风。",                     network: "语音识别当前不可用。",                              other: "语音输入意外停止。",                   privacy: "语音由您的浏览器处理，而非本网站。" },
        Urdu:       { start: "اپنا سوال بولیں",         stop: "املا روکیں",         listening: "سن رہا ہوں…",       denied: "مائیکروفون تک رسائی بند ہے۔ براؤزر کی ترتیبات میں فعال کریں۔",           noSpeech: "میں نے نہیں سنا۔ دوبارہ کوشش کریں۔",    noMic: "کوئی مائیکروفون نہیں ملا۔",         network: "آواز کی شناخت اس وقت دستیاب نہیں۔",                other: "املا غیر متوقع طور پر رک گیا۔",       privacy: "آواز آپ کا براؤزر پروسیس کرتا ہے، یہ سائٹ نہیں۔" },
        Portuguese: { start: "Dite a sua pergunta",     stop: "Parar ditado",       listening: "A ouvir…",          denied: "O acesso ao microfone está bloqueado. Ative-o nas definições.",          noSpeech: "Não percebi. Tente novamente.",         noMic: "Nenhum microfone encontrado.",      network: "O reconhecimento de voz está indisponível.",        other: "O ditado parou inesperadamente.",     privacy: "A voz é processada pelo seu navegador, não por este site." },
    },

    // Guided-workflow UI chrome. The questions themselves live in the spec
    // files (workflows/*.json) in whatever language they were authored; only
    // the surrounding buttons and labels are translated here. The selected
    // language is passed to the server, so follow-up questions come back
    // translated even when the spec is written in English.
    workflowTranslations: {
        English:    { heading: "Or start a guided walkthrough:",  resume: "Resume",      minutes: "~{n} min",    exit: "Exit",       copy: "Copy",   copied: "Copied",      download: "Download",    email: "Email it",    emailed: "Sent",       emailFailed: "Couldn't send",     progress: "Section {n} of {total} · {percent}%",    failed: "Something went wrong. Your answers are saved — try again." },
        French:     { heading: "Ou lancez un parcours guidé :",   resume: "Reprendre",   minutes: "~{n} min",    exit: "Quitter",    copy: "Copier", copied: "Copié",       download: "Télécharger", email: "Envoyer",     emailed: "Envoyé",     emailFailed: "Échec de l'envoi",  progress: "Section {n} sur {total} · {percent} %",  failed: "Une erreur est survenue. Vos réponses sont enregistrées — réessayez." },
        Spanish:    { heading: "O empieza un recorrido guiado:",  resume: "Reanudar",    minutes: "~{n} min",    exit: "Salir",      copy: "Copiar", copied: "Copiado",     download: "Descargar",   email: "Enviar",      emailed: "Enviado",    emailFailed: "No se pudo enviar", progress: "Sección {n} de {total} · {percent} %",   failed: "Algo salió mal. Tus respuestas están guardadas — inténtalo de nuevo." },
        Arabic:     { heading: "أو ابدأ جولة إرشادية:",           resume: "استئناف",     minutes: "~{n} دقيقة",  exit: "خروج",       copy: "نسخ",    copied: "تم النسخ",    download: "تنزيل",       email: "إرسال",       emailed: "تم الإرسال", emailFailed: "تعذر الإرسال",      progress: "القسم {n} من {total} · {percent}%",      failed: "حدث خطأ ما. إجاباتك محفوظة — حاول مرة أخرى." },
        Chinese:    { heading: "或开始引导式流程：",                 resume: "继续",         minutes: "约 {n} 分钟",  exit: "退出",        copy: "复制",    copied: "已复制",       download: "下载",         email: "发送邮件",     emailed: "已发送",      emailFailed: "发送失败",           progress: "第 {n} 节，共 {total} 节 · {percent}%",   failed: "出了点问题。您的回答已保存，请重试。" },
        Urdu:       { heading: "یا رہنمائی والا عمل شروع کریں:",  resume: "دوبارہ شروع", minutes: "~{n} منٹ",    exit: "باہر نکلیں", copy: "کاپی",   copied: "کاپی ہو گیا", download: "ڈاؤن لوڈ",    email: "ای میل کریں", emailed: "بھیج دیا",   emailFailed: "بھیجا نہیں جا سکا", progress: "سیکشن {n} از {total} · {percent}%",      failed: "کچھ غلط ہو گیا۔ آپ کے جوابات محفوظ ہیں — دوبارہ کوشش کریں۔" },
        Portuguese: { heading: "Ou inicie um percurso guiado:",   resume: "Retomar",     minutes: "~{n} min",    exit: "Sair",       copy: "Copiar", copied: "Copiado",     download: "Transferir",  email: "Enviar",      emailed: "Enviado",    emailFailed: "Falha ao enviar",   progress: "Secção {n} de {total} · {percent}%",     failed: "Algo correu mal. As suas respostas estão guardadas — tente de novo." },
    },
};
