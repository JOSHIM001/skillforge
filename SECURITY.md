# Security Policy

## ⚠️ Before You Do Anything — Read This

This project handles real API keys, JWT secrets, and database credentials.
**One mistake and your credentials are public on the internet forever.**

---

## 🔑 Credential Safety Rules

### Rule 1: The `.env` file NEVER gets committed

Your `.env` file is in `.gitignore`. But you can accidentally bypass this.

```bash
# This is safe (file is gitignored)
git add .

# This is DANGEROUS — force-adds gitignored files
git add -f .env           # NEVER DO THIS
git add --force .env      # NEVER DO THIS
```

**How to verify you're safe before every push:**

```bash
git status                # .env should NOT appear here
git diff --cached         # .env should NOT appear here
```

### Rule 2: Check your commits before pushing

```bash
git log --oneline -5      # see recent commits
git show HEAD             # see exactly what's in your last commit
```

If you accidentally committed `.env`, **do not push**. See the emergency section below.

### Rule 3: Every team member uses their own API keys

Never share your Groq/OpenAI/GitHub keys with teammates. Each person:
1. Creates their own free Groq account at [console.groq.com](https://console.groq.com)
2. Generates their own `SECRET_KEY` with `openssl rand -hex 32`
3. Keeps their `.env` local only

### Rule 4: Production keys go in GitHub Secrets

Go to: **repo → Settings → Secrets and variables → Actions**

Never paste production keys in:
- Code files
- Comments
- Issue comments
- PR descriptions
- Slack/Discord messages
- README files

---

## 🚨 Emergency: "I Accidentally Committed a Secret"

**Did you push yet?**

### If you haven't pushed yet:

```bash
# Undo the last commit (keeps your changes, just un-commits)
git reset HEAD~1

# Now remove the secret from the file, then recommit without it
```

### If you already pushed:

**The secret is compromised. Rotate it immediately — before anything else.**

1. **Revoke/rotate the leaked key first:**
   - Groq API key → [console.groq.com](https://console.groq.com) → delete old key, create new one
   - GitHub OAuth secret → [github.com/settings/developers](https://github.com/settings/developers) → regenerate secret
   - SECRET_KEY → generate a new one: `openssl rand -hex 32`

2. **Then clean the git history:**

```bash
# Install git-filter-repo (better than BFG)
pip install git-filter-repo

# Remove the file from entire history
git filter-repo --path .env --invert-paths

# Force push (requires admin access)
git push origin --force --all
```

3. **Tell the team** — they need to re-clone the repo after a force-push.

4. **Check if GitHub detected it** — GitHub has secret scanning. If your key was detected, you'll get an email. GitHub may have already invalidated the key (depending on the provider).

---

## 🔐 What Counts as a Secret (Never Commit These)

| Type | Example format |
|---|---|
| Groq API key | `gsk_...` |
| OpenAI API key | `sk-...` |
| GitHub OAuth secret | 40-char hex string |
| JWT SECRET_KEY | 64-char hex string |
| Database password | anything in `DATABASE_URL` |
| SSH private keys | `-----BEGIN ... PRIVATE KEY-----` |
| Any token/password/key | anything that sounds like one |

---

## 🛡️ Reporting a Security Vulnerability

If you find a security issue in the code (not a leaked key):

1. **Do NOT open a public GitHub issue**
2. Contact the project owner directly via private message
3. Include: what you found, how to reproduce it, potential impact

We aim to respond within 48 hours and fix critical issues within 7 days.
