// School configuration for the chat widget — edit this file to brand the
// frontend for your school. It must be loaded BEFORE chatbot.js (see
// chatbot_iframe.html). The backend has its own sibling file, school_config.py
// at the repo root.
window.SCHOOL_CONFIG = {
    // Shown in welcome text via the {school} placeholder below.
    schoolName: "Example School",

    // Contact address shown in the welcome screen and the contact banner.
    contactEmail: "admissions@example-school.org",

    // Base URL of the Flask chat server. "" means same origin as the page
    // serving chatbot_iframe.html; set e.g. "https://chatbot.example-school.org"
    // when the widget is hosted separately from the API.
    apiBase: "",

    // Namespace for localStorage keys (session id + saved chat history).
    // Change it if you run several chatbots on one domain.
    storagePrefix: "school_chatbot",

    // Welcome-screen text for the language selector. {school} is replaced with
    // schoolName at render time. Add or remove languages here AND in the
    // <select id="lang-select"> in chatbot_iframe.html.
    welcomeTranslations: {
        English:    { greeting: "Hi!",      intro: "I'm the {school} chatbot.",                          question: "What is your question?",      contactPre: "Please contact",                     contactPost: "if your question is not answered.",      disclaimer: "AI generated content. Always double check sources." },
        French:     { greeting: "Salut !",  intro: "Je suis le chatbot de {school}.",                    question: "Quelle est votre question ?", contactPre: "Veuillez contacter",                 contactPost: "si votre question n'a pas de réponse.",  disclaimer: "Contenu généré par l'IA. Vérifiez toujours les sources." },
        Spanish:    { greeting: "¡Hola!",   intro: "Soy el chatbot de {school}.",                        question: "¿Cuál es tu pregunta?",        contactPre: "Por favor contacte",                 contactPost: "si su pregunta no ha sido respondida.",  disclaimer: "Contenido generado por IA. Verifica siempre las fuentes." },
        Arabic:     { greeting: "مرحباً!", intro: "أنا روبوت المحادثة الخاص بـ {school}.",              question: "ما سؤالك؟",                    contactPre: "يرجى التواصل مع",                    contactPost: "إذا لم تتم الإجابة على سؤالك.",           disclaimer: "محتوى من إنشاء الذكاء الاصطناعي. تحقق دائمًا من المصادر." },
        Chinese:    { greeting: "你好！",   intro: "我是{school}的聊天机器人。",                          question: "您有什么问题？",                contactPre: "如果您的问题未得到解答，请联系",      contactPost: "。",                                      disclaimer: "AI 生成的内容。请始终核对来源。" },
        Urdu:       { greeting: "ہیلو!",    intro: "میں {school} کا چیٹ بوٹ ہوں۔",                      question: "آپ کا سوال کیا ہے؟",           contactPre: "اگر آپ کے سوال کا جواب نہیں ملا تو", contactPost: "سے رابطہ کریں۔",                          disclaimer: "اے آئی سے تیار کردہ مواد۔ ہمیشہ ذرائع کی تصدیق کریں۔" },
        Portuguese: { greeting: "Olá!",     intro: "Sou o chatbot de {school}.",                         question: "Qual é a sua pergunta?",       contactPre: "Por favor contacte",                 contactPost: "se a sua pergunta não foi respondida.",  disclaimer: "Conteúdo gerado por IA. Verifique sempre as fontes." },
    },
};
