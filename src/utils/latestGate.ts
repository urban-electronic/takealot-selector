/** 只认「最后一次」发起的那次请求的结果。
 *
 *  ## 为什么需要
 *
 * 切店铺时，上一家店的状态请求可能还在飞。它晚到的响应如果无条件写进 state，
 * 就会把**新店铺**的界面刷成**旧店铺**的数据：
 *
 * ```
 * t0  用户切到「代运营CK」→ 发起 status 请求（B）
 * t1  「自营店铺」那次请求（A）才返回 configured=true
 * t2  界面显示「密钥已配置」——但 CK 根本没配
 * ```
 *
 * 全程不报错、不闪烁，只是显示了一个错的结论。凭据这种东西显示错了，
 * 人会直接得出「已经配好了」的判断，所以必须按发起的先后丢弃过期响应。
 *
 * ## 用法
 *
 * ```ts
 * const gate = useRef(createLatestGate()).current;
 * const token = gate.begin();        // 发起时领号
 * const data = await fetch(...);
 * if (!gate.isLatest(token)) return; // 晚到的旧响应，丢掉
 * setState(data);
 * ```
 *
 * 计数器是单调递增的：后发起的请求一定拿到更大的号，所以不需要记录店铺 id，
 * 也不怕同一个店铺连点两次「重新检查」。
 */
export interface LatestGate {
  /** 领一个号，代表这次发起。返回的号要在响应回来时交回 `isLatest`。 */
  begin(): number;
  /** 这个号是不是仍然是最后一次发起的。false = 过期响应，应当丢弃。 */
  isLatest(token: number): boolean;
}

export function createLatestGate(): LatestGate {
  let seq = 0;
  return {
    begin: () => ++seq,
    isLatest: (token: number) => token === seq,
  };
}
