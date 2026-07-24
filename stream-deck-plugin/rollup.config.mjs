import commonjs from "@rollup/plugin-commonjs";
import resolve from "@rollup/plugin-node-resolve";
import typescript from "@rollup/plugin-typescript";

export default {
  input: "src/plugin.ts",
  output: {
    file: "com.lolswitcher.plugin.sdPlugin/bin/plugin.js",
    format: "cjs",
    sourcemap: false,
  },
  plugins: [
    typescript({
      tsconfig: "./tsconfig.json",
      outputToFilesystem: true,
    }),
    resolve({
      browser: false,
      exportConditions: ["node"],
      preferBuiltins: true,
    }),
    commonjs(),
  ],
  external: [],
};
