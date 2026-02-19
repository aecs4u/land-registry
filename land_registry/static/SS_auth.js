/**
 * Authentication Module using Clerk
 * Handles user authentication, session management, and protected features
 */

class AuthManager {
    constructor() {
        this.clerk = null;
        this.user = null;
        this.isInitialized = false;
        this.initPromise = null;
    }

    async initialize() {
        if (this.initPromise) {
            return this.initPromise;
        }

        this.initPromise = this._initializeClerk();
        return this.initPromise;
    }

    async _initializeClerk() {
        try {
            // Wait for Clerk to load
            await this._waitForClerk();

            if (!window.Clerk) {
                throw new Error('Clerk failed to load');
            }

            this.clerk = window.Clerk;

            // Initialize Clerk
            await this.clerk.load();

            this.isInitialized = true;
            this.user = this.clerk.user;

            // Listen for auth state changes
            this.clerk.addListener(({ user }) => {
                this.user = user;
                this._onAuthStateChange();
            });

            console.log('🔐 Authentication initialized');
            this._onAuthStateChange();

        } catch (error) {
            console.error('❌ Failed to initialize authentication:', error);
            this._showAuthError();
        }
    }

    async _waitForClerk(timeout = 10000) {
        const startTime = Date.now();

        while (!window.Clerk && (Date.now() - startTime) < timeout) {
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        if (!window.Clerk) {
            throw new Error('Clerk SDK did not load within timeout');
        }
    }

    _onAuthStateChange() {
        this._updateUI();
        this._toggleProtectedFeatures();
    }

    _updateUI() {
        const authContainer = document.getElementById('auth-container');
        const userInfo = document.getElementById('user-info');

        if (!authContainer) return;

        if (this.user) {
            // User is signed in
            authContainer.innerHTML = `
                <div class="user-section">
                    <div class="user-avatar">
                        <img src="${this.user.profileImageUrl}" alt="${this.user.firstName}" />
                    </div>
                    <div class="user-details">
                        <div class="user-name">${this.user.firstName} ${this.user.lastName}</div>
                        <div class="user-email">${this.user.primaryEmailAddress?.emailAddress}</div>
                    </div>
                    <div class="auth-actions">
                        <button onclick="authManager.showUserProfile()" class="profile-btn" title="View profile and drawing statistics">Profile</button>
                        <button onclick="authManager.openUserProfile()" class="profile-btn" title="Manage account settings">Settings</button>
                        <button onclick="authManager.signOut()" class="signout-btn">Sign Out</button>
                    </div>
                </div>
            `;

            if (userInfo) {
                userInfo.style.display = 'block';
            }
        } else {
            // User is not signed in
            authContainer.innerHTML = `
                <div class="auth-section">
                    <div class="auth-message">Sign in to save and sync your drawings</div>
                    <div class="auth-actions">
                        <button onclick="authManager.signIn()" class="signin-btn">Sign In</button>
                        <button onclick="authManager.signUp()" class="signup-btn">Sign Up</button>
                    </div>
                </div>
            `;

            if (userInfo) {
                userInfo.style.display = 'none';
            }
        }
    }

    _toggleProtectedFeatures() {
        // Enable/disable features that require authentication
        const protectedButtons = document.querySelectorAll('[data-requires-auth]');

        protectedButtons.forEach(button => {
            if (this.user) {
                button.disabled = false;
                button.title = '';
            } else {
                button.disabled = true;
                button.title = 'Sign in required';
            }
        });

        // Update save/load buttons specifically
        const saveBtn = document.getElementById('saveDrawingsBtn');
        const loadBtn = document.getElementById('loadDrawingsBtn');

        if (saveBtn && !this.user) {
            saveBtn.disabled = true;
            saveBtn.title = 'Sign in to save your drawings';
        }

        if (loadBtn && !this.user) {
            loadBtn.disabled = true;
            loadBtn.title = 'Sign in to load saved drawings';
        }
    }

    _showAuthError() {
        const authContainer = document.getElementById('auth-container');
        if (authContainer) {
            authContainer.innerHTML = `
                <div class="auth-error">
                    <div class="error-message">Authentication service unavailable</div>
                    <div class="error-note">Some features may be limited</div>
                </div>
            `;
        }
    }

    async signIn() {
        if (!this.clerk) {
            console.error('Clerk not initialized');
            return;
        }

        try {
            await this.clerk.openSignIn();
        } catch (error) {
            console.error('Sign in error:', error);
        }
    }

    async signUp() {
        if (!this.clerk) {
            console.error('Clerk not initialized');
            return;
        }

        try {
            await this.clerk.openSignUp();
        } catch (error) {
            console.error('Sign up error:', error);
        }
    }

    async signOut() {
        if (!this.clerk) {
            console.error('Clerk not initialized');
            return;
        }

        try {
            await this.clerk.signOut();
        } catch (error) {
            console.error('Sign out error:', error);
        }
    }

    async openUserProfile() {
        if (!this.clerk) {
            console.error('Clerk not initialized');
            return;
        }

        try {
            await this.clerk.openUserProfile();
        } catch (error) {
            console.error('Profile error:', error);
        }
    }

    isSignedIn() {
        return !!this.user;
    }

    getUser() {
        return this.user;
    }

    getUserId() {
        return this.user?.id;
    }

    async getToken() {
        if (!this.user) return null;

        try {
            return await this.user.getToken();
        } catch (error) {
            console.error('Failed to get token:', error);
            return null;
        }
    }

    // Enhanced API request with authentication
    async authenticatedFetch(url, options = {}) {
        const token = await this.getToken();

        if (!token) {
            throw new Error('Authentication required');
        }

        const authOptions = {
            ...options,
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json',
                ...options.headers
            }
        };

        const response = await fetch(url, authOptions);

        if (response.status === 401) {
            // Token expired, try to refresh
            await this.signIn();
            throw new Error('Authentication expired. Please sign in again.');
        }

        return response;
    }

    // User profile and dashboard methods
    async getUserProfile() {
        if (!this.isSignedIn()) {
            throw new Error('User not signed in');
        }

        try {
            const response = await this.authenticatedFetch('/api/v1/user/profile');
            return await response.json();
        } catch (error) {
            console.error('Failed to get user profile:', error);
            throw error;
        }
    }

    async getUserDrawings() {
        if (!this.isSignedIn()) {
            throw new Error('User not signed in');
        }

        try {
            const response = await this.authenticatedFetch('/api/v1/user/drawings');
            return await response.json();
        } catch (error) {
            console.error('Failed to get user drawings:', error);
            throw error;
        }
    }

    // Display user profile info in the UI
    async showUserProfile() {
        if (!this.isSignedIn()) {
            alert('Please sign in to view your profile');
            return;
        }

        try {
            const profileData = await this.getUserProfile();
            if (profileData.success) {
                const profile = profileData.profile;
                const message = `
User Profile:
• Drawing Sessions: ${profile.drawing_sessions}
• Total Features: ${profile.total_features}
• Latest Drawing: ${profile.latest_drawing || 'None'}

User ID: ${profile.user_id.substring(0, 8)}...
                `;
                alert(message);
            }
        } catch (error) {
            alert('Failed to load profile: ' + error.message);
        }
    }
}

// Create global auth manager instance
const authManager = new AuthManager();

// Initialize authentication when DOM is loaded
document.addEventListener('DOMContentLoaded', async function() {
    console.log('🔐 Initializing authentication...');
    await authManager.initialize();
});

// Make it globally available
window.authManager = authManager;