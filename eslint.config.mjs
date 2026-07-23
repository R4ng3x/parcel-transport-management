import eslint from "@eslint/js";
import owl from "@odoo/eslint-plugin-owl";
import globals from "globals";

const sourceFiles = ["addons/**/static/src/**/*.js"];

export default [
    {
        ignores: ["node_modules/**", ".venv/**"],
    },
    {
        files: sourceFiles,
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: "module",
            globals: {
                ...globals.browser,
                odoo: "readonly",
            },
        },
        plugins: {
            "@odoo/owl": owl,
        },
        rules: {
            ...eslint.configs.recommended.rules,
            "@odoo/owl/force-component-props-declaration": "error",
            "arrow-body-style": ["error", "as-needed"],
            curly: ["error", "all"],
            eqeqeq: ["error", "always"],
            "no-console": "error",
            "no-duplicate-imports": "error",
            "no-restricted-globals": ["error", "event", "self"],
            "no-restricted-syntax": ["error", "PrivateIdentifier"],
            "no-unused-vars": [
                "error",
                {
                    vars: "all",
                    args: "none",
                    ignoreRestSiblings: false,
                    caughtErrors: "all",
                },
            ],
            "prefer-const": [
                "error",
                {
                    destructuring: "all",
                    ignoreReadBeforeAssign: true,
                },
            ],
        },
    },
];
