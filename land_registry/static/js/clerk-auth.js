/**
 * Clerk Authentication Integration for AECS4U
 *
 * This module handles Clerk authentication integration with the FastAPI backend.
 * It manages the Clerk frontend SDK and syncs authentication state with the server.
 */

(function (window) {
  "use strict";

  const ClerkAuth = {
    initialized: false,
    clerk: null,
    config: null,

    /**
     * Initialize Clerk authentication
     * @param {Object} config - Clerk configuration
     * @param {string} config.publishableKey - Clerk publishable key
     * @param {string} config.afterSignInUrl - URL to redirect after sign in
     * @param {string} config.afterSignUpUrl - URL to redirect after sign up
     */
    async init(config) {
      if (this.initialized) {
        return;
      }

      this.config = config;

      if (!config.publishableKey) {
        console.warn("Clerk: No publishable key provided, authentication disabled");
        return;
      }

      try {
        // Wait for Clerk to be loaded
        await this.waitForClerk();

        // Initialize Clerk
        this.clerk = window.Clerk;
        await this.clerk.load();

        this.initialized = true;
        console.log("Clerk: Initialized successfully");

        // Set up event listeners
        this.setupEventListeners();

        // Check if user is already signed in
        if (this.clerk.user) {
          await this.syncSession();
        }
      } catch (error) {
        console.error("Clerk: Failed to initialize", error);
      }
    },

    /**
     * Wait for Clerk SDK to be available
     */
    waitForClerk() {
      return new Promise((resolve, reject) => {
        if (window.Clerk) {
          resolve();
          return;
        }

        let attempts = 0;
        const maxAttempts = 50; // 5 seconds max

        const checkClerk = setInterval(() => {
          attempts++;
          if (window.Clerk) {
            clearInterval(checkClerk);
            resolve();
          } else if (attempts >= maxAttempts) {
            clearInterval(checkClerk);
            reject(new Error("Clerk SDK not loaded"));
          }
        }, 100);
      });
    },

    /**
     * Set up Clerk event listeners
     */
    setupEventListeners() {
      // Listen for sign in/out events
      this.clerk.addListener((event) => {
        if (event.user) {
          this.onSignIn(event.user);
        } else {
          this.onSignOut();
        }
      });
    },

    /**
     * Handle sign in event
     */
    async onSignIn(user) {
      console.log("Clerk: User signed in", user.id);
      await this.syncSession();

      // Redirect if on auth pages
      const currentPath = window.location.pathname;
      if (
        currentPath === "/auth/login" ||
        currentPath === "/auth/register"
      ) {
        const redirectUrl =
          this.config.afterSignInUrl ||
          new URLSearchParams(window.location.search).get("next") ||
          "/dashboard";
        window.location.href = redirectUrl;
      }
    },

    /**
     * Handle sign out event
     */
    async onSignOut() {
      console.log("Clerk: User signed out");

      try {
        // Clear server session
        await fetch("/auth/clerk/logout", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
        });
      } catch (error) {
        console.error("Clerk: Failed to clear server session", error);
      }
    },

    /**
     * Sync Clerk session with server
     */
    async syncSession() {
      if (!this.clerk || !this.clerk.user) {
        return;
      }

      try {
        const token = await this.clerk.session.getToken();

        const response = await fetch("/auth/clerk/session", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        });

        if (response.ok) {
          const data = await response.json();
          console.log("Clerk: Session synced successfully");
          return data;
        } else {
          console.error("Clerk: Failed to sync session", await response.text());
        }
      } catch (error) {
        console.error("Clerk: Error syncing session", error);
      }
    },

    /**
     * Get the current session token
     */
    async getToken() {
      if (!this.clerk || !this.clerk.session) {
        return null;
      }
      return await this.clerk.session.getToken();
    },

    /**
     * Check if user is authenticated
     */
    isAuthenticated() {
      return this.clerk && this.clerk.user !== null;
    },

    /**
     * Get the current user
     */
    getUser() {
      return this.clerk ? this.clerk.user : null;
    },

    /**
     * Sign out the user
     */
    async signOut() {
      if (!this.clerk) {
        return;
      }

      try {
        await this.clerk.signOut();
        window.location.href = "/auth/login";
      } catch (error) {
        console.error("Clerk: Failed to sign out", error);
      }
    },

    /**
     * Open Clerk sign in modal
     */
    openSignIn() {
      if (!this.clerk) {
        window.location.href = "/auth/login";
        return;
      }
      this.clerk.openSignIn({
        afterSignInUrl: this.config.afterSignInUrl || "/dashboard",
        afterSignUpUrl: this.config.afterSignUpUrl || "/dashboard",
      });
    },

    /**
     * Open Clerk sign up modal
     */
    openSignUp() {
      if (!this.clerk) {
        window.location.href = "/auth/register";
        return;
      }
      this.clerk.openSignUp({
        afterSignInUrl: this.config.afterSignInUrl || "/dashboard",
        afterSignUpUrl: this.config.afterSignUpUrl || "/dashboard",
      });
    },

    /**
     * Open Clerk user profile modal
     */
    openUserProfile() {
      if (!this.clerk) {
        return;
      }
      this.clerk.openUserProfile();
    },

    /**
     * Mount Clerk sign in component
     * @param {string|HTMLElement} target - Target element or selector
     */
    mountSignIn(target) {
      if (!this.clerk) {
        console.warn("Clerk: Not initialized, cannot mount sign in");
        return;
      }

      const element =
        typeof target === "string" ? document.querySelector(target) : target;

      if (element) {
        this.clerk.mountSignIn(element, {
          afterSignInUrl: this.config.afterSignInUrl || "/dashboard",
          afterSignUpUrl: this.config.afterSignUpUrl || "/dashboard",
        });
      }
    },

    /**
     * Mount Clerk sign up component
     * @param {string|HTMLElement} target - Target element or selector
     */
    mountSignUp(target) {
      if (!this.clerk) {
        console.warn("Clerk: Not initialized, cannot mount sign up");
        return;
      }

      const element =
        typeof target === "string" ? document.querySelector(target) : target;

      if (element) {
        this.clerk.mountSignUp(element, {
          afterSignInUrl: this.config.afterSignInUrl || "/dashboard",
          afterSignUpUrl: this.config.afterSignUpUrl || "/dashboard",
        });
      }
    },

    /**
     * Mount Clerk user button component
     * @param {string|HTMLElement} target - Target element or selector
     */
    mountUserButton(target) {
      if (!this.clerk) {
        console.warn("Clerk: Not initialized, cannot mount user button");
        return;
      }

      const element =
        typeof target === "string" ? document.querySelector(target) : target;

      if (element) {
        this.clerk.mountUserButton(element, {
          afterSignOutUrl: "/auth/login",
        });
      }
    },

    /**
     * Make an authenticated API request
     * @param {string} url - API endpoint URL
     * @param {Object} options - Fetch options
     */
    async authenticatedFetch(url, options = {}) {
      const token = await this.getToken();

      const headers = {
        ...options.headers,
      };

      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }

      return fetch(url, {
        ...options,
        headers,
      });
    },
  };

  // Export to window
  window.ClerkAuth = ClerkAuth;

  // Auto-initialize if config is available in data attribute
  document.addEventListener("DOMContentLoaded", () => {
    const configElement = document.getElementById("clerk-config");
    if (configElement) {
      try {
        // First try to read from data attributes (preferred method)
        const publishableKey = configElement.dataset.publishableKey;
        if (publishableKey) {
          ClerkAuth.init({
            publishableKey: publishableKey,
            afterSignInUrl: configElement.dataset.afterSignInUrl || "/",
            afterSignUpUrl: configElement.dataset.afterSignUpUrl || "/",
            fallbackRedirectUrl: configElement.dataset.afterSignInUrl || "/",
          });
          return;
        }

        // Fallback: try to parse JSON from textContent
        const textContent = (configElement.textContent || "").trim();
        if (textContent && textContent !== "{}") {
          const config = JSON.parse(textContent);
          if (config.enabled && config.publishable_key) {
            ClerkAuth.init({
              publishableKey: config.publishable_key,
              afterSignInUrl: config.after_sign_in_url,
              afterSignUpUrl: config.after_sign_up_url,
              fallbackRedirectUrl: config.after_sign_in_url || "/",
            });
          }
        }
      } catch (error) {
        // Silent failure - auth will be handled by base.html script
        console.debug("ClerkAuth: Auto-init skipped -", error.message);
      }
    }
  });
})(window);
