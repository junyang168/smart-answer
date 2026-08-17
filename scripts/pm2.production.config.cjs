module.exports = {
  apps: [
    {
      name: process.env.SMART_ANSWER_PM2_APP || "smart-answer",
      cwd: process.env.SMART_ANSWER_WEB_ROOT,
      script: "npm",
      args: "run start -- -p 3000",
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
