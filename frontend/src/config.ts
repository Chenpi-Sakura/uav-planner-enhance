import apiConfig from '../../config.json'

/** 后端 API 基础地址，统一从仓库根目录 config.json 读取。 */
export const API_BASE = `http://localhost:${apiConfig.backend.port}`