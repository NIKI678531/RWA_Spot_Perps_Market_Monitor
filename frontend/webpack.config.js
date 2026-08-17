/* eslint-env node */
const path = require('path');
const webpack = require('webpack');
const HtmlWebpackPlugin = require('html-webpack-plugin');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');

/**
 * Where the app is mounted. Defaults to the domain root; the Docker build passes
 * FRONTEND_BASE_PATH so the same image can be served under a sub-path. Asset URLs and
 * the router basename both read it, or a sub-path deploy would load its bundle and
 * then 404 on every route.
 */
const basePath = process.env.FRONTEND_BASE_PATH || '/';

/**
 * The dev server proxies /api to the backend so the browser talks to one origin in
 * development exactly as it does behind nginx in Docker. Anything else would mean
 * CORS behaves differently in the two environments.
 */
module.exports = (_env, argv) => {
  const isProduction = argv.mode === 'production';

  return {
    entry: './src/index.tsx',
    output: {
      path: path.resolve(__dirname, 'dist'),
      filename: isProduction ? 'assets/[name].[contenthash:8].js' : 'assets/[name].js',
      chunkFilename: isProduction
        ? 'assets/[name].[contenthash:8].chunk.js'
        : 'assets/[name].chunk.js',
      publicPath: basePath,
      clean: true,
    },
    resolve: {
      extensions: ['.tsx', '.ts', '.jsx', '.js'],
      alias: { '@': path.resolve(__dirname, 'src') },
    },
    module: {
      rules: [
        {
          test: /\.tsx?$/,
          exclude: /node_modules/,
          use: {
            loader: 'ts-loader',
            options: { transpileOnly: true },
          },
        },
        {
          test: /\.css$/,
          use: [
            isProduction ? MiniCssExtractPlugin.loader : 'style-loader',
            'css-loader',
            'postcss-loader',
          ],
        },
        {
          test: /\.(png|jpe?g|gif|svg|woff2?)$/,
          type: 'asset/resource',
          generator: { filename: 'assets/[name].[hash:8][ext]' },
        },
      ],
    },
    plugins: [
      new HtmlWebpackPlugin({
        template: './public/index.html',
        favicon: undefined,
      }),
      new webpack.DefinePlugin({
        __BASE_PATH__: JSON.stringify(basePath),
      }),
      ...(isProduction
        ? [new MiniCssExtractPlugin({ filename: 'assets/[name].[contenthash:8].css' })]
        : []),
    ],
    optimization: {
      splitChunks: {
        cacheGroups: {
          // ECharts and antd together dwarf the application code; splitting them
          // keeps a page-level change from invalidating the whole bundle.
          vendor: {
            test: /[\\/]node_modules[\\/]/,
            name: 'vendor',
            chunks: 'all',
          },
        },
      },
    },
    devtool: isProduction ? 'source-map' : 'eval-cheap-module-source-map',
    devServer: {
      port: 3025,
      hot: true,
      historyApiFallback: true,
      proxy: [
        {
          context: ['/api'],
          target: 'http://localhost:8025',
          changeOrigin: true,
        },
      ],
    },
    performance: { hints: false },
  };
};
