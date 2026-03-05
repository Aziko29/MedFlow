/**
 * MEDFLOW - Professional Static Website
 * Version: 3.0
 * Author: MEDFLOW Team
 * All data stored in localStorage - No backend required
 */

// ===== DATA =====
const courses = [
    { 
        id: 'anatomy',
        name: 'Anatomiya', 
        category: 'Anatomy', 
        videos: 24, 
        tests: 120, 
        image: 'https://images.unsplash.com/photo-1579165466741-7f35e4755660?w=400&q=80',
        description: 'Inson anatomiyasi - organlar, tizimlar va tuzilmalar',
        icon: 'fa-heartbeat'
    },
    { 
        id: 'physiology',
        name: 'Fiziologiya', 
        category: 'Physiology', 
        videos: 32, 
        tests: 150, 
        image: 'https://images.unsplash.com/photo-1530026186672-2cd00ffc50fe?w=400&q=80',
        description: 'Organlar va tizimlarning ishlash prinsiplari',
        icon: 'fa-lungs'
    },
    { 
        id: 'pharmacology',
        name: 'Farmakologiya', 
        category: 'Pharmacology', 
        videos: 45, 
        tests: 200, 
        image: 'https://images.unsplash.com/photo-1471864190281-a93a3070b6de?w=400&q=80',
        description: 'Dorilar, ularning ta\'siri va qo\'llanilishi',
        icon: 'fa-pills'
    },
    { 
        id: 'cardiology',
        name: 'Kardiologiya', 
        category: 'Cardiology', 
        videos: 28, 
        tests: 140, 
        image: 'https://images.unsplash.com/photo-1579684385127-1ef15d508118?w=400&q=80',
        description: 'Yurak-qon tomir kasalliklari va ularni davolash',
        icon: 'fa-heart'
    },
    { 
        id: 'surgery',
        name: 'Xirurgiya', 
        category: 'Surgery', 
        videos: 36, 
        tests: 180, 
        image: 'https://images.unsplash.com/photo-1551028719-00167b16eac5?w=400&q=80',
        description: 'Jarrohlik usullari, operatsiyalar va asoratlar',
        icon: 'fa-scalpel'
    },
    { 
        id: 'neurology',
        name: 'Nevrologiya', 
        category: 'Neurology', 
        videos: 22, 
        tests: 110, 
        image: 'https://images.unsplash.com/photo-1559757175-0eb30cd8c063?w=400&q=80',
        description: 'Asab tizimi kasalliklari va ularni davolash',
        icon: 'fa-brain'
    }
];

const videos = {
    shorts: [
        { 
            title: 'Otitis media', 
            duration: '60 sec', 
            image: 'https://images.unsplash.com/photo-1581595219315-a187dd40c322?w=400&q=80', 
            videoUrl: 'https://www.youtube.com/embed/TiSpjfuQxXM',
            description: 'Otitis media etiology, symptoms, treatment in 60 seconds',
            views: 15420,
            likes: 1250
        },
        { 
            title: 'Myocardial infarction', 
            duration: '55 sec', 
            image: 'https://images.unsplash.com/photo-1612277795421-9bc7706a4a34?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/3JZ_D3ELwOQ',
            description: 'Heart attack signs, symptoms and emergency management',
            views: 12450,
            likes: 980
        },
        { 
            title: 'Pneumonia', 
            duration: '45 sec', 
            image: 'https://images.unsplash.com/photo-1581595219315-a187dd40c322?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/FjYjsftnFwA',
            description: 'Pneumonia types, causes and treatment',
            views: 9870,
            likes: 760
        },
        { 
            title: 'Diabetes mellitus', 
            duration: '60 sec', 
            image: 'https://images.unsplash.com/photo-1571772995611-d1c4b194ac9e?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/Xl9m7N5q_Sc',
            description: 'Diabetes types, symptoms and management',
            views: 21340,
            likes: 1840
        },
        { 
            title: 'Hypertension', 
            duration: '50 sec', 
            image: 'https://images.unsplash.com/photo-1612277795421-9bc7706a4a34?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/gK7sR0EfRkM',
            description: 'High blood pressure causes and treatment',
            views: 17890,
            likes: 1430
        },
        { 
            title: 'Asthma', 
            duration: '55 sec', 
            image: 'https://images.unsplash.com/photo-1581595219315-a187dd40c322?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/P2pBzN0nz9U',
            description: 'Asthma symptoms, triggers and medications',
            views: 14560,
            likes: 1120
        }
    ],
    lectures: [
        { 
            title: 'Heart failure', 
            duration: '12 min', 
            image: 'https://images.unsplash.com/photo-1612277795421-9bc7706a4a34?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/0VqT8cF7Q1k',
            description: 'Heart failure pathophysiology and management',
            views: 8760,
            likes: 690
        },
        { 
            title: 'Renal system', 
            duration: '15 min', 
            image: 'https://images.unsplash.com/photo-1581595219315-a187dd40c322?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/-lZpvAHdtjM',
            description: 'Kidney diseases and renal disorders',
            views: 6540,
            likes: 520
        },
        { 
            title: 'Liver diseases', 
            duration: '18 min', 
            image: 'https://images.unsplash.com/photo-1571772995611-d1c4b194ac9e?w=400&q=80',
            videoUrl: 'https://www.youtube.com/embed/6h9xIx9aZ_Y',
            description: 'Hepatitis, cirrhosis and liver disorders',
            views: 7890,
            likes: 630
        }
    ]
};

const quizzes = {
    anatomy: [
        { question: "Which bone is known as the 'shoulder blade'?", options: ["Clavicle", "Scapula", "Humerus", "Sternum"], correct: 1, explanation: "Scapula - yelka suyagi" },
        { question: "How many bones are in the adult human body?", options: ["206", "208", "210", "204"], correct: 0, explanation: "Voyaga yetgan odamda 206 ta suyak bor" },
        { question: "Which is the longest bone in the human body?", options: ["Femur", "Tibia", "Humerus", "Fibula"], correct: 0, explanation: "Femur - son suyagi, eng uzun suyak" },
        { question: "What is the smallest bone in the body?", options: ["Stapes", "Incus", "Malleus", "Hyoid"], correct: 0, explanation: "Stapes - uzangi suyagi, quloqda" },
        { question: "How many vertebrae are in the spinal column?", options: ["33", "30", "26", "24"], correct: 0, explanation: "Umurtqa pog'onasida 33 ta umurtqa" }
    ],
    cardiology: [
        { question: "Which chamber pumps blood to the body?", options: ["Right atrium", "Left atrium", "Right ventricle", "Left ventricle"], correct: 3, explanation: "Chap qorincha qonni tanaga haydaydi" },
        { question: "What is normal adult heart rate?", options: ["40-50", "60-100", "100-120", "120-140"], correct: 1, explanation: "Normal yurak urishi 60-100/min" },
        { question: "Which artery is most commonly blocked?", options: ["LAD", "RCA", "LCX", "PDA"], correct: 0, explanation: "LAD - left anterior descending artery" },
        { question: "What does ECG stand for?", options: ["Electrocardiogram", "Echo", "Electrogram", "Cardiogram"], correct: 0, explanation: "ECG - elektrokardiogramma" },
        { question: "Which valve is between left atrium and ventricle?", options: ["Aortic", "Mitral", "Tricuspid", "Pulmonary"], correct: 1, explanation: "Mitral qopqoq chap bo'lmacha va qorincha orasida" }
    ],
    pharma: [
        { question: "Which drug is used for hypertension?", options: ["Lisinopril", "Ibuprofen", "Amoxicillin", "Omeprazole"], correct: 0, explanation: "Lisinopril - ACE inhibitor, bosimni tushiradi" },
        { question: "What class is Metformin?", options: ["Biguanide", "Sulfonylurea", "DPP-4", "SGLT2"], correct: 0, explanation: "Metformin - biguanide class" },
        { question: "Which is a beta-blocker?", options: ["Metoprolol", "Amlodipine", "Losartan", "HCTZ"], correct: 0, explanation: "Metoprolol - beta-blokator" },
        { question: "What does Warfarin do?", options: ["Anticoagulant", "Antiplatelet", "Thrombolytic", "Analgesic"], correct: 0, explanation: "Warfarin - antikoagulyant, qon suyultiradi" },
        { question: "Which antibiotic covers MRSA?", options: ["Vancomycin", "Penicillin", "Ciprofloxacin", "Doxycycline"], correct: 0, explanation: "Vankomitsin MRSA ga qarshi ishlatiladi" }
    ]
};

const library = [
    { id: 'anatomy', icon: 'fa-book', title: 'Anatomy Atlas', desc: 'PDF, 45 pages', url: '#' },
    { id: 'guidelines', icon: 'fa-file-pdf', title: 'Clinical Guidelines 2026', desc: 'WHO protocols', url: '#' },
    { id: 'pharma', icon: 'fa-capsules', title: 'Pharmacology Handbook', desc: 'PDF, 120 pages', url: '#' },
    { id: 'surgery', icon: 'fa-scalpel', title: 'Surgical Techniques', desc: 'Video + PDF', url: '#' },
    { id: 'research', icon: 'fa-flask', title: 'Research Papers', desc: 'Latest studies', url: '#' },
    { id: 'case', icon: 'fa-notes-medical', title: 'Clinical Cases', desc: '50+ cases', url: '#' }
];

// ===== STATE MANAGEMENT =====
let currentState = {
    section: 'home',
    quiz: {
        currentQuiz: quizzes.anatomy,
        currentQuestion: 0,
        userAnswers: [],
        score: 0,
        completed: false
    },
    user: {
        isLoggedIn: false,
        name: '',
        email: '',
        role: 'guest',
        progress: {
            watchedVideos: [],
            testResults: [],
            streak: 0,
            lastActive: null
        }
    },
    videoType: 'shorts',
    darkMode: false
};

// ===== UTILITY FUNCTIONS =====
function escapeHTML(str) {
    if (!str) return '';
    return str.replace(/[&<>"]/g, function(match) {
        if (match === '&') return '&amp;';
        if (match === '<') return '&lt;';
        if (match === '>') return '&gt;';
        if (match === '"') return '&quot;';
        return match;
    });
}

function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function showLoading(show = true) {
    const loader = document.getElementById('globalLoader');
    if (loader) {
        if (show) {
            loader.classList.add('active');
        } else {
            loader.classList.remove('active');
        }
    }
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    notification.setAttribute('role', 'alert');
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.classList.add('show');
    }, 10);
    
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// ===== LOCAL STORAGE =====
function saveState() {
    try {
        localStorage.setItem('medflow_state', JSON.stringify(currentState));
    } catch (e) {
        console.error('Error saving state:', e);
    }
}

function loadState() {
    try {
        const saved = localStorage.getItem('medflow_state');
        if (saved) {
            const parsed = JSON.parse(saved);
            currentState.user = parsed.user || currentState.user;
            currentState.videoType = parsed.videoType || 'shorts';
            
            // Update UI based on login state
            if (currentState.user.isLoggedIn) {
                updateAuthUI();
            }
        }
    } catch (e) {
        console.error('Error loading state:', e);
    }
}

// ===== MOBILE MENU =====
function toggleMobileMenu() {
    const navLinks = document.getElementById('navLinks');
    navLinks.classList.toggle('show');
}

function closeMobileMenu() {
    document.getElementById('navLinks').classList.remove('show');
}

// ===== NAVIGATION =====
function showSection(sectionId) {
    showLoading(true);
    
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.getElementById(sectionId).classList.add('active');
    
    document.querySelectorAll('.nav-links a').forEach(a => {
        a.classList.remove('active-nav');
        if (a.textContent.trim().toLowerCase().includes(sectionId.toLowerCase()) || 
            (sectionId === 'home' && a.textContent.trim() === 'Home')) {
            a.classList.add('active-nav');
        }
    });
    
    currentState.section = sectionId;
    closeMobileMenu();
    saveState();
    
    setTimeout(() => {
        if (sectionId === 'courses') loadCourses();
        if (sectionId === 'videos') loadVideos(currentState.videoType);
        if (sectionId === 'quiz') loadQuiz('anatomy');
        if (sectionId === 'dashboard') loadDashboard();
        if (sectionId === 'pricing') loadPricing();
        if (sectionId === 'library') loadLibrary();
        if (sectionId === 'home') loadHomeData();
        
        showLoading(false);
    }, 300);
}

// ===== HOME SECTION =====
function loadHomeData() {
    loadFeatures();
    loadStats();
    loadPreviewCourses();
}

function loadFeatures() {
    const grid = document.getElementById('featuresGrid');
    if (!grid) return;
    
    const features = [
        { icon: 'fa-bolt', title: 'Microlearning', desc: '30-60 soniyalik videolar bilan tez va samarali o\'rganish' },
        { icon: 'fa-robot', title: 'AI Recommendations', desc: 'Sizning zaif joylaringizni aniqlab, shaxsiy tavsiyalar beradi' },
        { icon: 'fa-chart-line', title: 'Progress Tracking', desc: 'O\'z taraqqiyotingizni kuzatib boring' },
        { icon: 'fa-brain', title: 'Spaced Repetition', desc: 'Ma\'lumotlarni uzoq muddatli xotiraga joylash' }
    ];
    
    grid.innerHTML = features.map(f => `
        <div class="feature-card">
            <div class="feature-icon"><i class="fas ${f.icon}"></i></div>
            <h3>${f.title}</h3>
            <p>${f.desc}</p>
        </div>
    `).join('');
}

function loadStats() {
    const grid = document.getElementById('statsGrid');
    if (!grid) return;
    
    const stats = [
        { number: '120K', label: 'Students' },
        { number: '500K', label: 'Videos watched' },
        { number: '98%', label: 'Satisfaction' }
    ];
    
    grid.innerHTML = stats.map(s => `
        <div class="stat-item">
            <div class="number">${s.number}</div>
            <div>${s.label}</div>
        </div>
    `).join('');
}

function loadPreviewCourses() {
    const grid = document.getElementById('previewCourses');
    if (!grid) return;
    
    const preview = courses.slice(0, 2);
    grid.innerHTML = preview.map(course => `
        <div class="course-card" onclick="showSection('courses')">
            <div class="course-image" style="background-image: url('${course.image}')"></div>
            <div class="course-content">
                <span class="course-category">${course.category}</span>
                <h3>${course.name}</h3>
                <div class="course-meta">
                    <span><i class="fas fa-video"></i> ${course.videos} videos</span>
                </div>
            </div>
        </div>
    `).join('');
}

// ===== COURSES SECTION =====
function loadCourses() {
    const grid = document.getElementById('coursesGrid');
    if (!grid) return;
    
    grid.innerHTML = courses.map(course => `
        <div class="course-card" onclick="openCourse('${course.id}')">
            <div class="course-image" style="background-image: url('${course.image}')"></div>
            <div class="course-content">
                <span class="course-category">${course.category}</span>
                <h3>${course.name}</h3>
                <p>${course.description}</p>
                <div class="course-meta">
                    <span><i class="fas fa-video"></i> ${course.videos} videos</span>
                    <span><i class="fas fa-question-circle"></i> ${course.tests} tests</span>
                </div>
            </div>
        </div>
    `).join('');
}

function openCourse(courseId) {
    const course = courses.find(c => c.id === courseId);
    if (course) {
        showNotification(`📘 ${course.name} kursi ochilmoqda`, 'info');
        showSection('videos');
    }
}

// ===== VIDEOS SECTION =====
function loadVideos(type) {
    currentState.videoType = type;
    const grid = document.getElementById('videosGrid');
    if (!grid) return;
    
    const videoList = type === 'shorts' ? videos.shorts : videos.lectures;
    
    grid.innerHTML = videoList.map(v => `
        <button class="short-item" style="background-image: url('${v.image}')" 
                onclick="playVideo('${v.title}', '${v.videoUrl}')">
            <i class="fas fa-play-circle" style="font-size: 2rem;"></i>
            <strong>${v.title}</strong>
            <small>${v.duration}</small>
            <small><i class="fas fa-eye"></i> ${formatNumber(v.views)}</small>
        </button>
    `).join('');
    
    saveState();
}

function filterVideos(type, element) {
    document.querySelectorAll('.video-tab').forEach(t => t.classList.remove('active'));
    element.classList.add('active');
    loadVideos(type);
}

function playVideo(title, videoUrl) {
    document.getElementById('modalVideoTitle').textContent = title;
    document.getElementById('modalVideoIframe').src = videoUrl + '?autoplay=1';
    document.getElementById('videoModal').classList.add('active');
    document.body.style.overflow = 'hidden';
    
    if (currentState.user.isLoggedIn) {
        currentState.user.progress.watchedVideos.push({
            title: title,
            date: new Date().toISOString()
        });
        saveState();
    }
}

function closeVideoModal() {
    document.getElementById('videoModal').classList.remove('active');
    document.getElementById('modalVideoIframe').src = '';
    document.body.style.overflow = '';
}

function playDemo() {
    playVideo('MEDFLOW Demo', 'https://www.youtube.com/embed/TiSpjfuQxXM');
}

// ===== LIBRARY SECTION =====
function loadLibrary() {
    const grid = document.getElementById('libraryGrid');
    if (!grid) return;
    
    grid.innerHTML = library.map(item => `
        <div class="library-item" onclick="openPDF('${item.id}')">
            <i class="fas ${item.icon}"></i>
            <h3>${item.title}</h3>
            <p>${item.desc}</p>
        </div>
    `).join('');
}

function openPDF(type) {
    showNotification(`📚 ${type} - Yuklanmoqda...`, 'info');
}

// ===== QUIZ SECTION =====
function loadQuiz(topic, element) {
    if (!quizzes[topic]) return;
    
    showLoading(true);
    
    currentState.quiz.currentQuiz = quizzes[topic];
    currentState.quiz.currentQuestion = 0;
    currentState.quiz.userAnswers = new Array(currentState.quiz.currentQuiz.length).fill(null);
    currentState.quiz.score = 0;
    currentState.quiz.completed = false;
    
    if (element) {
        document.querySelectorAll('.quiz-tabs .btn').forEach(btn => {
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-outline');
        });
        element.classList.remove('btn-outline');
        element.classList.add('btn-primary');
    }
    
    setTimeout(() => {
        displayQuestion();
        showLoading(false);
        saveState();
    }, 300);
}

function displayQuestion() {
    if (!currentState.quiz.currentQuiz || !currentState.quiz.currentQuiz[currentState.quiz.currentQuestion]) return;
    
    const q = currentState.quiz.currentQuiz[currentState.quiz.currentQuestion];
    const content = document.getElementById('quizContent');
    const result = document.getElementById('quizResult');
    
    if (!content) return;
    
    if (result) result.innerHTML = '';
    
    content.innerHTML = `
        <div class="quiz-question">
            <h3>${currentState.quiz.currentQuestion + 1}. ${q.question}</h3>
        </div>
        <div class="quiz-options">
            ${q.options.map((opt, idx) => `
                <button class="quiz-option ${currentState.quiz.userAnswers[currentState.quiz.currentQuestion] === idx ? 'selected' : ''}" 
                        onclick="selectAnswer(${idx})"
                        ${currentState.quiz.completed ? 'disabled' : ''}>
                    ${String.fromCharCode(65 + idx)}. ${opt}
                </button>
            `).join('')}
        </div>
    `;
    
    document.getElementById('questionCounter').textContent = 
        `${currentState.quiz.currentQuestion + 1} / ${currentState.quiz.currentQuiz.length}`;
    
    document.getElementById('prevBtn').disabled = currentState.quiz.currentQuestion === 0;
}

function selectAnswer(idx) {
    if (currentState.quiz.completed) return;
    
    currentState.quiz.userAnswers[currentState.quiz.currentQuestion] = idx;
    displayQuestion();
    saveState();
}

function nextQuestion() {
    if (currentState.quiz.completed) return;
    
    if (currentState.quiz.currentQuestion < currentState.quiz.currentQuiz.length - 1) {
        currentState.quiz.currentQuestion++;
        displayQuestion();
    } else {
        calculateScore();
        showResults();
    }
    saveState();
}

function prevQuestion() {
    if (currentState.quiz.completed) return;
    
    if (currentState.quiz.currentQuestion > 0) {
        currentState.quiz.currentQuestion--;
        displayQuestion();
        saveState();
    }
}

function calculateScore() {
    let correct = 0;
    currentState.quiz.userAnswers.forEach((answer, index) => {
        if (answer === currentState.quiz.currentQuiz[index].correct) {
            correct++;
        }
    });
    currentState.quiz.score = correct;
    currentState.quiz.completed = true;
}

function showResults() {
    const total = currentState.quiz.currentQuiz.length;
    const percentage = (currentState.quiz.score / total) * 100;
    
    const result = document.getElementById('quizResult');
    if (!result) return;
    
    result.innerHTML = `
        <div class="quiz-result ${percentage >= 70 ? 'success' : 'warning'}">
            <h3>✅ Test yakunlandi!</h3>
            <p>To'g'ri javoblar: ${currentState.quiz.score} / ${total}</p>
            <p>Natija: ${percentage.toFixed(1)}%</p>
            ${percentage >= 70 ? 
                '<p>🎉 Tabriklaymiz! Siz muvaffaqiyatli o\'tdingiz.</p>' : 
                '<p>💪 Qayta urinib ko\'ring. Zaif mavzularni takrorlang.</p>'}
            <button class="btn btn-primary" onclick="restartQuiz()">Qayta boshlash</button>
        </div>
    `;
    
    if (currentState.user.isLoggedIn) {
        currentState.user.progress.testResults.push({
            topic: currentState.quiz.currentQuiz[0].question.substring(0, 20),
            score: currentState.quiz.score,
            total: total,
            date: new Date().toISOString()
        });
        saveState();
    }
}

function restartQuiz() {
    currentState.quiz.completed = false;
    currentState.quiz.currentQuestion = 0;
    currentState.quiz.userAnswers = new Array(currentState.quiz.currentQuiz.length).fill(null);
    currentState.quiz.score = 0;
    displayQuestion();
    document.getElementById('quizResult').innerHTML = '';
    saveState();
}

// ===== DASHBOARD SECTION =====
function loadDashboard() {
    const stats = document.getElementById('dashboardStats');
    const content = document.getElementById('dashboardContent');
    
    if (!currentState.user.isLoggedIn) {
        if (stats) stats.innerHTML = '';
        if (content) {
            content.innerHTML = `
                <div class="dashboard-login-prompt">
                    <i class="fas fa-user-circle"></i>
                    <h3>Dashboardni ko'rish uchun tizimga kiring</h3>
                    <button class="btn btn-primary" onclick="showLoginModal()">
                        <i class="fas fa-sign-in-alt"></i> Kirish
                    </button>
                </div>
            `;
        }
        return;
    }
    
    const watchedCount = currentState.user.progress.watchedVideos.length;
    const testsPassed = currentState.user.progress.testResults.length;
    const avgScore = currentState.user.progress.testResults.length > 0 ?
        Math.round(currentState.user.progress.testResults.reduce((acc, t) => acc + (t.score / t.total * 100), 0) / 
        currentState.user.progress.testResults.length) : 0;
    
    if (stats) {
        stats.innerHTML = `
            <div class="stat-card">
                <div class="number">${watchedCount}</div>
                <div>Videos watched</div>
            </div>
            <div class="stat-card">
                <div class="number">${testsPassed}</div>
                <div>Tests passed</div>
            </div>
            <div class="stat-card">
                <div class="number">${avgScore}%</div>
                <div>Average score</div>
            </div>
            <div class="stat-card">
                <div class="number">${currentState.user.progress.streak}</div>
                <div>Day streak</div>
            </div>
        `;
    }
    
    if (content) {
        const weakTopics = [
            { name: 'Farmakologiya', score: 65 },
            { name: 'Kardiologiya', score: 80 },
            { name: 'Nevrologiya', score: 45 }
        ];
        
        content.innerHTML = `
            <h3>Zaif mavzular</h3>
            <div style="margin: 20px 0;">
                ${weakTopics.map(topic => `
                    <div style="margin-bottom: 15px;">
                        <div style="display: flex; justify-content: space-between;">
                            <span>${topic.name}</span>
                            <span>${topic.score}%</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: ${topic.score}%;"></div>
                        </div>
                    </div>
                `).join('')}
            </div>
            
            <h3>Sizga tavsiya</h3>
            <div class="shorts-grid" style="grid-template-columns: 1fr 1fr;">
                <button class="short-item" style="background: var(--accent); color: white; min-height: 100px;" onclick="showSection('videos')">
                    <i class="fas fa-brain"></i> Nevrologiya asoslari
                </button>
                <button class="short-item" style="background: var(--primary); color: white; min-height: 100px;" onclick="showSection('videos')">
                    <i class="fas fa-pills"></i> Beta blokatorlar
                </button>
            </div>
            
            <div style="margin-top: 30px;">
                <h3>Oxirgi faollik</h3>
                <p>${currentState.user.progress.lastActive ? 
                    new Date(currentState.user.progress.lastActive).toLocaleDateString('uz-UZ') : 
                    'Hali faollik yo\'q'}</p>
            </div>
        `;
    }
}

// ===== PRICING SECTION =====
function loadPricing() {
    const grid = document.getElementById('pricingGrid');
    if (!grid) return;
    
    grid.innerHTML = `
        <div class="pricing-card">
            <h3>Free</h3>
            <div class="price">0 so'm</div>
            <ul class="features">
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Shorts videolar</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Cheklangan testlar (10/kun)</li>
                <li><i class="fas fa-times" style="color: #ff4444;"></i> To'liq ma'ruzalar</li>
                <li><i class="fas fa-times" style="color: #ff4444;"></i> Flashkartalar</li>
            </ul>
            <button class="btn btn-outline btn-block" onclick="selectPlan('free')">Boshlash</button>
        </div>
        
        <div class="pricing-card featured">
            <div class="featured-badge">🔥 Eng ommabop</div>
            <h3>Student Premium</h3>
            <div class="price">45,000 so'm <small>/oy</small></div>
            <ul class="features">
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Barcha videolar</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> To'liq ma'ruzalar</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Cheksiz testlar</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Flashkartalar</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Offline rejim</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> AI tavsiyalar</li>
            </ul>
            <button class="btn btn-primary btn-block" onclick="selectPlan('premium')">Tanlash</button>
        </div>
        
        <div class="pricing-card">
            <h3>Doctor Pro</h3>
            <div class="price">120,000 so'm <small>/oy</small></div>
            <ul class="features">
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Barcha Premium</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Klinik holatlar</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> WHO qo'llanmalar</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> CME materiallari</li>
                <li><i class="fas fa-check" style="color: var(--accent);"></i> Doktorlar klubi</li>
            </ul>
            <button class="btn btn-outline btn-block" onclick="selectPlan('pro')">Tanlash</button>
        </div>
    `;
}

function selectPlan(plan) {
    if (!currentState.user.isLoggedIn) {
        showNotification(`Iltimos, avval tizimga kiring`, 'warning');
        showLoginModal();
        return;
    }
    
    const prices = {
        free: 0,
        premium: 45000,
        pro: 120000
    };
    
    showNotification(`✅ ${plan} rejasi tanlandi. To'lov: ${formatNumber(prices[plan])} so'm/oy`, 'success');
}

// ===== AUTHENTICATION =====
function showLoginModal() {
    document.getElementById('loginModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeLoginModal() {
    document.getElementById('loginModal').classList.remove('active');
    document.body.style.overflow = '';
}

function showRegisterModal() {
    closeLoginModal();
    document.getElementById('registerModal').classList.add('active');
}

function closeRegisterModal() {
    document.getElementById('registerModal').classList.remove('active');
    document.body.style.overflow = '';
}

function handleLogin(event) {
    event.preventDefault();
    
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    
    if (!email || !password) {
        showNotification('Email va parolni kiriting', 'warning');
        return;
    }
    
    showLoading(true);
    
    setTimeout(() => {
        currentState.user.isLoggedIn = true;
        currentState.user.email = email;
        currentState.user.name = email.split('@')[0];
        currentState.user.progress.lastActive = new Date().toISOString();
        
        saveState();
        showNotification(`Xush kelibsiz, ${currentState.user.name}!`, 'success');
        closeLoginModal();
        
        if (currentState.section === 'dashboard') {
            loadDashboard();
        }
        
        showLoading(false);
        updateAuthUI();
    }, 1000);
}

function handleRegister(event) {
    event.preventDefault();
    
    const name = document.getElementById('registerName').value;
    const email = document.getElementById('registerEmail').value;
    const password = document.getElementById('registerPassword').value;
    const role = document.getElementById('registerRole').value;
    
    if (!name || !email || !password) {
        showNotification('Barcha maydonlarni to\'ldiring', 'warning');
        return;
    }
    
    showLoading(true);
    
    setTimeout(() => {
        currentState.user.isLoggedIn = true;
        currentState.user.name = name;
        currentState.user.email = email;
        currentState.user.role = role;
        currentState.user.progress = {
            watchedVideos: [],
            testResults: [],
            streak: 0,
            lastActive: new Date().toISOString()
        };
        
        saveState();
        showNotification(`Tabriklaymiz, ${name}! Ro'yxatdan o'tish muvaffaqiyatli`, 'success');
        closeRegisterModal();
        
        if (currentState.section === 'dashboard') {
            loadDashboard();
        }
        
        showLoading(false);
        updateAuthUI();
    }, 1000);
}

function logout() {
    currentState.user.isLoggedIn = false;
    currentState.user = {
        isLoggedIn: false,
        name: '',
        email: '',
        role: 'guest',
        progress: {
            watchedVideos: [],
            testResults: [],
            streak: 0,
            lastActive: null
        }
    };
    
    saveState();
    showNotification('Tizimdan chiqildi', 'info');
    updateAuthUI();
    
    if (currentState.section === 'dashboard') {
        loadDashboard();
    }
}

function updateAuthUI() {
    const loginBtn = document.getElementById('loginBtn');
    if (loginBtn) {
        if (currentState.user.isLoggedIn) {
            loginBtn.innerHTML = `<i class="fas fa-user-circle"></i> ${currentState.user.name}`;
            loginBtn.onclick = () => showUserMenu();
        } else {
            loginBtn.innerHTML = `<i class="fas fa-sign-in-alt"></i> Login`;
            loginBtn.onclick = () => showLoginModal();
        }
    }
}

function showUserMenu() {
    const existingMenu = document.querySelector('.user-menu');
    if (existingMenu) {
        existingMenu.remove();
        return;
    }
    
    const menu = document.createElement('div');
    menu.className = 'user-menu';
    menu.innerHTML = `
        <div class="user-menu-content">
            <p><strong>${currentState.user.name}</strong></p>
            <p>${currentState.user.email}</p>
            <p>Role: ${currentState.user.role}</p>
            <hr>
            <button onclick="logout()" class="btn btn-outline btn-block">Chiqish</button>
        </div>
    `;
    
    document.body.appendChild(menu);
    
    setTimeout(() => {
        document.addEventListener('click', function closeMenu(e) {
            if (!menu.contains(e.target) && !e.target.closest('#loginBtn')) {
                menu.remove();
                document.removeEventListener('click', closeMenu);
            }
        });
    }, 100);
}

// ===== INITIALIZATION =====
document.addEventListener('DOMContentLoaded', function() {
    loadState();
    
    loadHomeData();
    loadCourses();
    loadVideos('shorts');
    loadDashboard();
    loadPricing();
    loadLibrary();
    
    document.querySelectorAll('.nav-links a').forEach(a => {
        if (a.textContent.trim() === 'Home') {
            a.classList.add('active-nav');
        }
    });
    
    document.getElementById('currentYear').textContent = new Date().getFullYear();
    
    updateAuthUI();
    
    console.log('✅ MEDFLOW initialized');
});

// ===== EXPORT FUNCTIONS =====
window.showSection = showSection;
window.toggleMobileMenu = toggleMobileMenu;
window.closeMobileMenu = closeMobileMenu;
window.filterVideos = filterVideos;
window.playVideo = playVideo;
window.closeVideoModal = closeVideoModal;
window.playDemo = playDemo;
window.loadQuiz = loadQuiz;
window.selectAnswer = selectAnswer;
window.nextQuestion = nextQuestion;
window.prevQuestion = prevQuestion;
window.openCourse = openCourse;
window.showLoginModal = showLoginModal;
window.closeLoginModal = closeLoginModal;
window.showRegisterModal = showRegisterModal;
window.closeRegisterModal = closeRegisterModal;
window.handleLogin = handleLogin;
window.handleRegister = handleRegister;
window.logout = logout;
window.selectPlan = selectPlan;
window.openPDF = openPDF;
window.showNotification = showNotification;