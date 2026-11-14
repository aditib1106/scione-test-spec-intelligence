import { defineRailway, project, service, volume } from "railway/iac";
// This partial owns the web service and its existing data volume. Keep both
// resources declared. MySQL and secret variables remain separately managed.
export const partial = "scione-web";
export default defineRailway(() => {
  // These match the already-created volume; omitting them would reset live
  // volume properties. Keep this resource and attachment during future edits.
  const data = volume("scione-web-volume", { region: "sfo", sizeMB: 500 });
  const scioneWeb = service("scione-web", {
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/api/health",
    healthcheckTimeout: 300,
    volumeMounts: {
      "/data": data,
    },
    replicas: 1,
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 3,
    },
  });

  return project("sweet-possibility", {
    variables: { managed: false },
    resources: [scioneWeb, data],
  });
});
