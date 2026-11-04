// Vite matches the full request URL, including its query string.
export const ROBOT_API_PROXY_PATTERN = "^/api/(status|map|video|mode|command|emergency-stop|speed|avoidance|mode-obstacle-stop-distance|mapping/.*|navigation/.*)(?:\\?.*)?$";
