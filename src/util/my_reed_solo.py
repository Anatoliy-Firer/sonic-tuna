import numpy as np
from numba import int32, njit
from numba.experimental import jitclass

# --- GF(256) primitives ---
_prim = 0x11D
_gf_exp = None
_gf_log = None

@njit(fastmath=True)
def _init_tables():
    exp = np.zeros(512, dtype=np.uint8)
    log = np.zeros(256, dtype=np.int32)
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= _prim
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


@njit(fastmath=True)
def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return int(_gf_exp[int(_gf_log[a]) + int(_gf_log[b])])


@njit(fastmath=True)
def _gf_div(a, b):
    if b == 0:
        raise ZeroDivisionError("GF(256) division by zero")
    if a == 0:
        return 0
    return int(_gf_exp[(int(_gf_log[a]) - int(_gf_log[b])) % 255])


@njit(fastmath=True)
def _gf_pow(a, power):
    if power == 0:
        return 1
    if a == 0:
        return 0
    return int(_gf_exp[(int(_gf_log[a]) * power) % 255])


@njit(fastmath=True)
def _gf_pow_alpha(power):
    return int(_gf_exp[power % 255])


@njit(fastmath=True)
def _gf_inverse(a):
    if a == 0:
        raise ZeroDivisionError("GF(256) inverse of zero")
    return int(_gf_exp[255 - int(_gf_log[a])])


_gf_exp, _gf_log = _init_tables()

_my_reed_solo_spec = [
    ("nsym", int32),
    ("nsize", int32),
    ("k", int32),
    ("gen", int32[:]),
]

@jitclass(_my_reed_solo_spec)
class MyReedSolo:
    def __init__(self):
        self.nsym = 32
        self.nsize = 255
        self.k = 223
        self.gen = self._rs_generator_poly(self.nsym)

    # --- Polynomial helpers (highest degree first) ---
    def _gf_poly_add(self, p, q):
        if len(p) < len(q):
            p = [0] * (len(q) - len(p)) + p
        else:
            q = [0] * (len(p) - len(q)) + q
        return [a ^ b for a, b in zip(p, q)]

    def _gf_poly_scale(self, p, x):
        return [_gf_mul(c, x) for c in p]

    def _gf_poly_mul(self, p, q):
        res = [0] * (len(p) + len(q) - 1)
        for i, cp in enumerate(p):
            if cp == 0:
                continue
            for j, cq in enumerate(q):
                if cq == 0:
                    continue
                res[i + j] ^= _gf_mul(cp, cq)
        return res

    def _gf_poly_eval(self, poly, x):
        y = 0
        for c in poly:
            y = _gf_mul(y, x) ^ c
        return y

    def _gf_poly_derivative(self, poly):
        if len(poly) <= 1:
            return [0]
        res = []
        deg = len(poly) - 1
        for i, c in enumerate(poly[:-1]):
            power = deg - i
            if power % 2 == 1:
                res.append(c)
        return res if res else [0]

    # --- Reed-Solomon core ---
    def _rs_generator_poly(self, nsym):
        g = [1]
        for i in range(nsym):
            # (x - a) in GF(2^m) is (x + a); highest-degree-first => [1, a]
            g = self._gf_poly_mul(g, [1, _gf_pow_alpha(i)])
        return np.array(g, dtype=np.int32)

    def _rs_calc_syndromes(self, msg, nsym):
        # synd[0] is a padding 0 to match common RS formulations (S1..Snsym)
        synd = [0]
        for i in range(nsym):
            synd.append(self._gf_poly_eval(msg, _gf_pow_alpha(i)))
        return synd

    def _rs_find_error_locator(self, synd, nsym):
        err_loc = [1]
        old_loc = [1]
        for i in range(nsym):
            delta = synd[i]
            for j in range(1, len(err_loc)):
                delta ^= _gf_mul(err_loc[-(j + 1)], synd[i - j])
            old_loc.append(0)
            if delta != 0:
                if len(old_loc) > len(err_loc):
                    new_loc = self._gf_poly_scale(old_loc, delta)
                    old_loc = self._gf_poly_scale(err_loc, _gf_inverse(delta))
                    err_loc = new_loc
                err_loc = self._gf_poly_add(err_loc, self._gf_poly_scale(old_loc, delta))
        while len(err_loc) > 1 and err_loc[0] == 0:
            err_loc = err_loc[1:]
        return err_loc

    def _rs_find_errors(self, err_loc, nmess):
        err_pos = []
        for i in range(nmess):
            # Chien search: roots at alpha^{-i}
            if self._gf_poly_eval(err_loc, _gf_pow_alpha(255 - i)) == 0:
                err_pos.append(nmess - 1 - i)
        if len(err_pos) != len(err_loc) - 1:
            return None
        return err_pos

    def _rs_find_errata_locator(self, err_pos, nmess):
        err_loc = [1]
        for p in err_pos:
            i = nmess - 1 - p
            # error locator uses (1 - x * a) => [a, 1] in highest-degree-first
            err_loc = self._gf_poly_mul(err_loc, [_gf_pow_alpha(i), 1])
        return err_loc

    def _rs_find_error_evaluator(self, synd, err_loc, nsym):
        synd_rev = synd[::-1]
        err_eval = self._gf_poly_mul(synd_rev, err_loc)
        return err_eval[-nsym:]

    def _rs_correct_errata(self, msg, synd, err_pos):
        nmess = len(msg)
        if not err_pos:
            return msg[:]
        # Solve for error magnitudes using a GF(256) Vandermonde system.
        # This is reliable for small t (<=16) and avoids convention pitfalls.
        m = len(err_pos)
        if m > len(synd):
            return None
        # Xi = alpha^(n-1-p)
        X = [_gf_pow_alpha(nmess - 1 - p) for p in err_pos]
        # Build matrix A (m x m) where A[r][c] = X_c^r, r=0..m-1
        A = [[0] * m for _ in range(m)]
        for r in range(m):
            for c in range(m):
                A[r][c] = _gf_pow(X[c], r)
        B = list(synd[:m])
        # Gaussian elimination in GF(256)
        for i in range(m):
            if A[i][i] == 0:
                swap = None
                for k in range(i + 1, m):
                    if A[k][i] != 0:
                        swap = k
                        break
                if swap is None:
                    return None
                A[i], A[swap] = A[swap], A[i]
                B[i], B[swap] = B[swap], B[i]
            inv = _gf_inverse(A[i][i])
            for j in range(i, m):
                A[i][j] = _gf_mul(A[i][j], inv)
            B[i] = _gf_mul(B[i], inv)
            for k in range(m):
                if k == i:
                    continue
                if A[k][i] != 0:
                    factor = A[k][i]
                    for j in range(i, m):
                        A[k][j] ^= _gf_mul(factor, A[i][j])
                    B[k] ^= _gf_mul(factor, B[i])
        # Apply corrections
        msg_out = msg[:]
        for p, magnitude in zip(err_pos, B):
            msg_out[p] ^= magnitude
        return msg_out

    def _encode_block(self, block):
        msg = np.zeros(self.nsize, dtype=np.int32)
        for i in range(self.k):
            msg[i] = int(block[i])
        for i in range(self.k):
            coef = int(msg[i])
            if coef != 0:
                for j, gj in enumerate(self.gen):
                    msg[i + j] ^= _gf_mul(int(gj), coef)
        out = np.empty(self.nsize, dtype=np.uint8)
        for i in range(self.k):
            out[i] = np.uint8(block[i])
        for i in range(self.nsym):
            out[self.k + i] = np.uint8(msg[self.k + i])
        return out

    def _decode_block(self, block):
        msg = list(block)
        synd = self._rs_calc_syndromes(msg, self.nsym)
        if max(synd) == 0:
            return msg[:self.k]
        err_loc = self._rs_find_error_locator(synd[1:], self.nsym)
        if (len(err_loc) - 1) * 2 > self.nsym:
            return msg[:self.k]
        err_pos = self._rs_find_errors(err_loc, len(msg))
        if err_pos is None:
            return msg[:self.k]
        corrected = self._rs_correct_errata(msg, synd[1:], err_pos)
        if corrected is None:
            return msg[:self.k]
        synd2 = self._rs_calc_syndromes(corrected, self.nsym)
        if max(synd2) != 0:
            return msg[:self.k]
        return corrected[:self.k]

    # --- Public API ---
    def encode(self, data):
        arr = np.asarray(data, dtype=np.uint8).reshape(-1)
        if arr.size == 0:
            return np.empty(0, dtype=np.uint8)
        nblocks = (arr.size + self.k - 1) // self.k
        padded = np.zeros(nblocks * self.k, dtype=np.uint8)
        padded[:arr.size] = arr
        out = np.empty(nblocks * self.nsize, dtype=np.uint8)
        for bi in range(nblocks):
            block = padded[bi * self.k:(bi + 1) * self.k]
            encoded = self._encode_block(block)
            out[bi * self.nsize:(bi + 1) * self.nsize] = encoded
        return out

    def decode(self, data):
        arr = np.asarray(data, dtype=np.uint8).reshape(-1)
        if arr.size == 0:
            return np.empty(0, dtype=np.uint8)
        nblocks = (arr.size + self.nsize - 1) // self.nsize
        padded = np.zeros(nblocks * self.nsize, dtype=np.uint8)
        padded[:arr.size] = arr
        out = np.empty(nblocks * self.k, dtype=np.uint8)
        for bi in range(nblocks):
            block = padded[bi * self.nsize:(bi + 1) * self.nsize]
            decoded = self._decode_block(block)
            for i in range(self.k):
                out[bi * self.k + i] = np.uint8(decoded[i])
        return out


if __name__ == "__main__":
    import random
    rs = MyReedSolo()
    random_data = np.random.randint(0, 255, 1115, dtype=np.uint8)
    encoded = rs.encode(random_data)
    for i in range(10):
        encoded[random.randint(0, len(encoded)-1)] = random.randint(0, 255)
    decoded = rs.decode(encoded)
    print(np.array_equal(random_data, decoded))
